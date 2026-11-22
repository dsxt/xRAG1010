Steer 要插进你现在这条还在跑的循环里，不能再开一条流。你现在的 `fba:ai:stream:{conversation_id}` 锁会把第二条消息直接挡成「这个会话正在回复中」，所以先得有一条旁路，把消息送进正在跑的那一轮。

## 注入点

安全边界只有一个：当前这次工具调用执行完，下一次请求模型之前。

`NativeLoopEngine.run` 里，模型一次可以吐出多个 `tool_calls`。OpenAI 兼容协议要求这条 `assistant` 消息里的每个 `call_id` 都先有一条 `tool` 结果，后面才能再放 `user`。所以：

- 正在执行的那个工具让它跑完，不中途杀掉。
- 同一批里还没开始的调用不再执行，各补一条「未执行」的 `tool` 结果，把这批调用补齐。
- 然后把 Steer 消息追加进 `messages`，进入下一轮模型请求。
- 模型还在吐字时不要切。等这一轮流结束，知道有没有工具调用之后再处理。

正在打最终回答、没有工具调用时也算边界：收件箱里有 Steer 就不发 `DoneEvent`，把用户消息接上，再请求一次模型。

## 三种投递

收件箱里的每条消息带一个 `delivery`：

| delivery | 循环里怎么处理 |
|---|---|
| `steer` | 在上面的边界注入，还是同一轮 |
| `queue` | 循环不看它。发出 `DoneEvent` 之后，由 `chat_service` 在同一条 SSE 上接着开下一轮 |
| `interrupt` | 和 Steer 一样把没开始的工具补成「未执行」，然后这条消息作为新目标。正在跑的工具仍然跑完 |

确认卡片是硬边界。`paused_for_confirm` 时不要 Steer 绕过卡片，消息留在队列里，等用户点完确认再送。写操作已经进了二次确认，中途改口也不该替用户把那次变更执行掉或悄悄取消。

## 要加的东西

**运行登记。** 按 `conversation_id` 挂一个活着的 run：一个 `asyncio.Queue`，加上当前轮次。放在 `chat_service` 里，引擎通过 `RunContext` 拿到这个队列。`ports.py` 不能 import 引擎，队列类型定义在 `core/`。

Redis 锁继续留着，保证一个会话只有一条模型循环。变的是锁住之后，新消息不再回 `busy`，而是入队。

**新接口。** `POST /ai/chat/stream` 只负责开工并占住 SSE。运行中再发消息走 `POST /ai/chat/messages`，body 是 `conversation_id`、`content`、`delivery`。这条请求只入队，立刻返回，不新开模型循环。原来的 SSE 继续往下推。

**循环改动**，就在 `engines/native.py` 执行工具的那段：

```python
messages.append(assistant_with_tool_calls)
pending = list(calls)

while pending:
    if inbox.has_steer_or_interrupt():
        for skipped in pending:
            payload = json.dumps(
                {"skipped": True, "reason": "用户中途改了指示，此调用未执行"},
                ensure_ascii=False,
            )
            messages.append({"role": "tool", "tool_call_id": skipped.call_id, "content": payload})
            yield ToolResultEvent(..., ok=False, summary="已跳过：收到新的指示")
        pending.clear()
        break

    call = pending.pop(0)
    outcome = await tool_dispatcher.dispatch(call, ctx)  # 已经开始的，等它结束
    messages.append({"role": "tool", "tool_call_id": call.call_id, "content": outcome.payload})
    yield ToolResultEvent(...)

steered = inbox.drain_steers()  # 这一拍到的多条，按到达顺序一次取完
if steered:
    messages.append({
        "role": "user",
        "content": (
            "用户在你执行过程中补充了指示。已经完成的工具调用仍然有效，"
            "尚未开始的调用没有执行。请从当前进度继续，并遵守下面的新指示：\n"
            + "\n".join(m.content for m in steered)
        ),
    })
    continue  # 不要 Done，直接下一轮

if not tool_buffer:
    yield DoneEvent()
```

每次请求模型之前再查一次收件箱。边界只设在「工具返回之后」的话，用户刚好在模型吐字时发来的消息要多等一整轮。

注入给模型的文字要用固定前缀，说明旧的工具结果仍然有效。不然模型容易把这句当成全新问题，把刚才查到的东西丢掉重来。

`pydantic_ai_engine` 先不动。策略放在 `RunContext` 上之后，另一个引擎以后在自己的工具边界取同一个队列即可。

## 落库和界面

- Steer 消息在注入成功时再写入 `ai_message`，不要一入队就写。运行中途崩了，模型没看到的话，历史里不该有这句话。
- 同一轮里连续来的几条 Steer 合成一次注入，按到达顺序拼接，不要每条都重新请求一次模型。
- 前端在 `running` 时，Enter 发 `delivery=steer`，Ctrl+Enter 发 `delivery=queue`。Steer 立刻出现在对话里；Queue 先出现在输入框下方的队列，这一轮结束后再进对话。
- SSE 不用新事件类型也能工作。跳过的调用走现有的 `tool_result`（`ok=false`）。界面上的用户气泡由前端乐观画出来。

Steer 仍算在 `max_tool_rounds` 里。它是同一轮的延续，不另给预算，否则可以靠不停 Steer 把轮次上限绕开。

## 先写这四条测试

1. 模型一次给出三个工具调用，第二个执行期间收到 Steer：第一个的结果保留，第二个跑完，第三个是「未执行」，下一轮模型请求里能看到 Steer 正文。
2. 正在生成最终回答时收到 Steer：不发 `done`，再请求一轮模型。
3. `delivery=queue` 在工具循环里不会被读到，`done` 之后才成为下一轮的用户消息。
4. 已经停在确认卡片上时，Steer 不执行、也不取消这次变更，消息留在队列里。
[TOC]

# FlowExecutor 执行器

## 第1章 任务目标

到目前为止，我们已经造好了所有"零件"：

- 流程定义（YAML 加载成 `FlowsList`）
- 对话状态（`DialogueState`、各种 Context）
- 命令处理（`CommandProcessor` 改 state）
- 各种动作（`Action` 及其子类）

但还差最关键的一步：**谁把这些零件串起来跑？** 谁去读 state、按 YAML 一步步推进、在该执行 action 时调 ActionRunner、在该等用户时停下来？

这就是这一节的主角——**`FlowExecutor`**。

了解 FlowExecutor 在整个系统中的位置，有助于理解它的职责边界。

<img src="images/09-FlowExecutor位置.png" style="zoom: 67%;" />

## 第2章 整合FlowExecutor

### 2.1 创建FlowExecutor

创建文件： `atguigu/task/flow/executor.py`

```python
# atguigu/task/flow/executor.py

from atguigu.domain.state import DialogueState
from atguigu.task.flow.flows import FlowsList
from atguigu.task.action.runner import ActionRunner

class FlowExecutor:
    """
    流程执行器：推进yaml中定义的业务任务流程以及系统任务流程
    """

    async def run_task(self,state: DialogueState,flows: FlowsList,action_runner: ActionRunner):

        pass
```

### 2.2 修改TaskHandler

回顾上一节 并完善`TaskHandler` 的两步：`CommandProcessor` 改完状态就退场，`FlowExecutor` 接手，读取改好的 `state`，按 `YAML` 流程一步步推进，每遇到 `action` 步骤就交给 `ActionRunner` 执行。

完善文件`atguigu/task/handler.py`，添加 `flow_executor` 和步骤2

```python
# atguigu/task/handler.py

from atguigu.domain.messages import BotMessage
from atguigu.domain.state import DialogueState
from atguigu.task.action.runner import ActionRunner
from atguigu.task.command.models import Command
from atguigu.task.command.processor import CommandProcessor
from atguigu.task.flow.executor import FlowExecutor
from atguigu.task.flow.flows import FlowsList

class TaskHandler:

    def __init__(
            self,
            flows: FlowsList,
            command_processor: CommandProcessor,
            action_runner: ActionRunner,
            flow_executor: FlowExecutor
    ):
        self.flows = flows
        self.command_processor = command_processor
        self.action_runner = action_runner
        self.flow_executor = flow_executor

    async def handle(self, commands: list[Command], state: DialogueState) -> list[BotMessage]:
        # 阶段1：CommandProcessor 进行状态的修改
        self.command_processor.run(commands, state, self.flows)

        # 阶段2：FlowExecutor 进行任务/流程的推进
        messages = await self.flow_executor.run_task(state, self.flows, self.action_runner)

        # 返回流程执行器得到的消息
        return messages
```

### 2.3 依赖注入

文件 `atguigu/api/routers/dependencies.py` 中完善 `DialogueEngine` 中的 `task_handler` 配置

```python
# atguigu/api/routers/dependencies.py

return DialogueEngine(
    turn_planner = TurnPlanner(),
    task_handler = TaskHandler(
        flows=flow_list,
        command_processor=CommandProcessor(),
        action_runner=build_action_runner(),
        flow_executor=FlowExecutor()
    ),
    knowledge_handler = KnowledgeHandler(knowledge_intents = KNOWLEDGE_INTENTS),
    clarify_responder = ClarifyResponder(),
    turn_plan_validator = TurnPlanValidator()
)
```

## 第3章 run_task：外层循环

### 3.1 代码

文件： `atguigu/task/flow/executor.py` 

```python
# atguigu/task/flow/executor.py

async def run_task(self,state: DialogueState,flows: FlowsList,action_runner: ActionRunner):

    messages: list[BotMessage] = []
    while True:  # 找要执行的流程步骤

        # 1. 推进流程以及内部step，当step的type类型是action是从advance_until_action中退出
        action_call: ActionCall = self.advance_until_action(state, flows)

        # 2. 当action_name是action_listen的时候，结束流程，并返回消息，等待下一轮的用户输入
        if action_call.action_name == "action_listen":
            break
        else:

            # 3. 如果是其他类型的action，则执行action
            action_result: ActionResult = await action_runner.run(action_call, state)
            state.set_slots(action_result.slot_updates)
            messages.extend(action_result.messages)

    # 4. 返回消息，等待下一轮的用户输入
    return messages
```

### 3.2 三件事

外层循环每一轮做三件事：

1. **找下一个要执行的 action**：调 `advance_until_action`，它会沿流程推进，遇到action则返回，最终返回一个 `ActionCall`
2. **判断要不要退场**：如果拿到的是 `action_listen`，说明该等用户的下一轮输入了，break 退出循环
3. **真正干活**：如果不是`action_listen`，则交给 `ActionRunner` 执行；把 action 返回的**槽位更新**写回 state、把消息添加到消息列表

<img src="images/09-外层循环.png" style="zoom: 33%;" />

## 第4章 advance_until_action：内层循环

这是 FlowExecutor 的**心脏**。它沿着 YAML 流程图,跳过不需要执行 action 的步骤，遇到要执行 action 的步骤就返回。

### 4.1 代码

文件： `atguigu/task/flow/executor.py` 

```python
# atguigu/task/flow/executor.py
def advance_until_action(self, state: DialogueState, flows: FlowsList) -> ActionCall:

    while True:

        # 1. 获取当前任务上下文对象：系统任务优先
        current_active_task = state.current_active_task()

        # 2. 如果当前没有任务，手动返回action_listen，等待用户输入
        # 两种典型情况：
        # - 业务流程刚跑完 end 步骤，active_task 被清空，又没有系统过场
        # - 用户刚启动会话，根本还没开任何任务
        if current_active_task is None:
            return ActionCall(action_name="action_listen")

        # 3. 获取当前流程对象
        flow = flows.get_flow_by_id(current_active_task.flow_id)

        # 4. 获取当前step
        step = flow.get_step_by_id(current_active_task.step_id)

        # 5. 运行当前step
        action_call = self._run_step(state, step, flows)

        # 6. 如果step的类型是action,退出while true
        if action_call is not None:
            return action_call
```

### 4.2 没有当前任务的情形

`current_active_taskis None` 时直接返回 `action_listen`——没事可做，等用户输入。

两种典型情况：

- 业务流程刚跑完 `end` 步骤，`active_task` 被清空，又没有系统过场
- 用户刚启动会话，根本还没开任何任务

### 4.3 内层循环做什么

<img src="images/09-内层循环流程图.png" style="zoom: 67%;" />

### 4.4 _run_step：按 step 类型分发

文件：`atguigu/task/flow/executor.py`

```python
# atguigu/task/flow/executor.py
def _run_step(self, state: DialogueState, step: FlowStep, flows: FlowsList) -> ActionCall | None:

    if isinstance(step, StartFlowStep):
        return self._run_start_step(step, state)
    if isinstance(step, EndFlowStep):
        return self._run_end_step(state)
    if isinstance(step, CollectFlowStep):
        return self._run_collect_step(step, state, flows)
    if isinstance(step, ActionFlowStep):
        return self._run_action_step(step, state)
```

四种 step 各有处理方法和不同的返回值。返回 `None` 表示"这一步不产生 action，请继续推进"，返回 `ActionCall` 表示"该执行这个 action 了"。

四种 step 的处理 vs 返回值：

| step 类型 | 处理 | 返回 None 还是 ActionCall |
| --- | --- | --- |
| `start` | 跳到 next step | None（继续推） |
| `end` | 结束当前 flow | None（继续推） |
| `collect` | 自动补槽 + 槽位判断 | 视情况 |
| `action` | 推进 + 构造 ActionCall | **ActionCall** |

后面四章逐个拆解这四种 step 的处理。

## 第5章 处理 start step（最复杂）

### 5.1 代码

文件：`atguigu/task/flow/executor.py`

```python
# atguigu/task/flow/executor.py
def _run_start_step(self, step: StartFlowStep, state: DialogueState) -> None:
    # 1. 推进下一步
    self._advance_next_step(state, step)
    # 2. 返回None
    return None
```

`start` 步骤是流程的**入口标记**。处理两件事：把当前任务的 `step_id` 设成 next step，然后返回 None 让内层循环继续推进流程向后执行。

### 5.2 配合 YAML 看

退款流程的开头：

```yaml
- id: start
  type: start
  next: ask_order_number 
```

当前 step 是 `start` 时，`_advance_to_next_step` 会读 `next: ask_order_number`，把 task 的 `step_id` 改成 `ask_order_number`。下一轮内层循环就从 `ask_order_number` 这个步骤开始处理。

### 5.3 推进的本质

`_advance_to_next_step` 是所有 step 推进的公用方法。

文件：`atguigu/task/flow/executor.py`

```python
# atguigu/task/flow/executor.py
def _advance_next_step(self, state, step):
    # 1. 寻找下一个step id
    next_step_id = self._select_next_step(step, state)
    # 2. 更新当前任务上下文的step_id(给当前执行任务流程的上下文用)不做这个动作，出不来
    state.current_active_task().step_id = next_step_id
```

它的本质：**改 `step_id`**。流程"推进"在代码里就是这么简单——把当前任务记的 step_id 改成下一个 step 的 id。

### 5.4 选择下一步

`_select_next_step` 负责从 `next` 链接中挑出目标 step，条件跳转的核心逻辑就在这里。

文件：`atguigu/task/flow/executor.py`

```python
# atguigu/task/flow/executor.py
def _select_next_step(self, step: FlowStep, state: DialogueState) -> str:

    for link in step.next:
        if isinstance(link, StaticLink):
            return link.target  # 下一个步骤的id
        if isinstance(link, ConditionalLink):
            if self._eval_condition(state, link.condition):
                return link.target
        if isinstance(link, FallbackLink):
            return link.target
    return "没有下一步"
```

回顾流程加载那一节：每个 step 的 `next` 在加载时被解析成一个 `FlowStepLink` 列表。这里就是按列表遍历，决定走哪个 target：

| 链接类型 | 处理 |
| --- | --- |
| `StaticLink`（YAML 写 `next: xxx`） | 直接返回 target |
| `ConditionalLink`（YAML 写 `- if: ...`） | 表达式为真就返回 target，否则跳过看下一个分支 |
| `FallbackLink`（YAML 写 `- else: ...`） | 直接返回 target（之前的条件都没满足时的兜底） |

回顾推荐相似商品流程，正好用到条件跳转：

```yaml
- id: start
  type: start
  next:
    - if: "slots.get('product_id')"        # 有商品就推荐
      then: respond
    - else: missing_product_context        # 没有就提示先选商品
```

`_select_next_step` 跑下来，会让 `start step` 根据 `slots.get('product_id')` 走向不同的下一步。

### 5.5 计算条件表达式

文件：`atguigu/task/flow/executor.py`

```python
# atguigu/task/flow/executor.py
def _eval_condition(self, state: DialogueState, condition: str) -> bool:
    data = {
        "slots": state.active_task.slots,
        # current_active_task：获取当前任务上下文对象：系统任务优先
        # model_dump：对象转字典
        "context": state.current_active_task().model_dump(mode="json")
    }
    return bool(eval(condition, {'__builtins__': None}, data))
```

#### 5.5.1 代码执行 eval

##### 测试evel

`eval` 是 Python 内置函数，能把**字符串当作代码来执行**。

三个参数分别是：

- `condition`：要执行的字符串，比如 `"slots.get('product_id')"` 这种 YAML 里写的条件
- `{}`：全局变量，这里给空字典，意思是"不允许访问任何全局变量"，比较安全
- `data`：局部变量，也就是代码里能"看到"哪些变量, `data` 是一个字典

```python
# atguigu/test/evel/test1.py
condition = "a>b"
data = {"a": 3, "b": 2}
print(eval(condition, data))
```

另一个例子

```python
# atguigu/test/evel/test2.py
condition = "context.get('reason') == 'xyz'"
data = {
    "context": {"reason": "abc"}
}
print(bool(eval(condition,  data)))
```

##### 安全执行

###### 场景一：如果不限制全局变量（危险）

```python
# atguigu/test/evel/test3.py

# 假设全局变量不限制，eval 能访问 Python 的所有内置功能
condition = "__import__('os').system('del important_file.txt')"
data = {"slots": {}}
result = eval(condition, data)  # 这会把你的文件删掉！
```

###### 场景二： 限制全局变量（安全）

为了"安全"，不给它任何全局能力，只让条件字符串能访问我们主动放进 `data` 的变量。

```python
# atguigu/test/evel/test4.py
condition = "__import__('os').system('del important_file.txt')"
data = {"slots": {}}

# 禁止执行内部函数
result = eval(condition, {'__builtins__': None}, data)
# ❌ 报错！
# 因为全局变量字典被禁用，__import__ 这个函数不存在
```

###### 生活化类比

想象你给小孩一个房间让他玩：

| 做法                                            | 类比                                                         |
| ----------------------------------------------- | ------------------------------------------------------------ |
| `eval(condition)` 不限制                        | 小孩能打开房间里任何柜子，包括刀具柜、药品柜，很危险         |
| `eval(condition, {'__builtins__': None}, data)` | 只给他 `data` 这个玩具箱，房间其他柜子全锁死，他只能玩玩具箱里的 `slots` 和 `context` 两个玩具 |

## 第6章 处理 end step（最简单）

### 6.1 代码

文件：`atguigu/task/flow/executor.py`

```python
# atguigu/task/flow/executor.py
def _run_end_step(self, state: DialogueState) -> None:
    if state.active_system_task:
        # 清空state中系统任务流程上下文
        state.end_active_system_task()
    else:
        # 清空state中业务任务流程上下文
        state.end_active_task()
    return None
```

### 6.2 业务结束和系统过场结束

`end` 步骤标志一个 flow 跑完了，按"当前是系统流还是业务流"分别清掉。

因为两种 end 的语义完全不同。系统流（如过场"好的，先处理退款"）是一个**临时叠加层**，它结束时业务任务还在下面等着，所以只清系统任务。

| 当前 | 处理 |
| --- | --- |
| 系统任务（如"先处理退款"过场跑完） | `end_active_system_task()` 清掉系统过场，业务流接着跑 |
| 业务任务（如退款流程到 end） | `end_active_task()` 清掉活跃任务 |

清掉之后返回 None。下一轮内层循环再调 `state.current_active_task()`：

- 业务结束 → 就没事了，返回 `action_listen`
- 系统过场结束 → 业务任务还在，继续推进业务

## 第7章 处理 action step

### 7.1 代码

文件：`atguigu/task/flow/executor.py`

```python
# atguigu/task/flow/executor.py
def _run_action_step(self, step: ActionFlowStep, state: DialogueState) -> ActionCall:
    # 1. 推进下一步
    self._advance_next_step(state, step)
    # 2. 构造 ActionCall
    action_call  = self._build_action_call(state, step) 
    # 3. 退出内层让外层执行
    return action_call 
```

### 7.2 构造 ActionCall

文件：`atguigu/task/flow/executor.py`

```python
# atguigu/task/flow/executor.py
def _build_action_call(self, state, step) -> ActionCall:
    # 1. 获取action_name (action_listen/action_response/action_xxx)
    # 2. 获取action_kwargs (构建参数)
    action_name = step.action
    action_kwargs = step.args
    # action_kwargs有可能有:结构有可能是一个str、dict、或者空字典{}
    # str: "context.response"
    if isinstance(action_kwargs, str):
        action_kwargs = state.active_system_task.model_dump(mode="json")[action_kwargs.split(".")[1]]
    return ActionCall(action_name=action_name, action_kwargs=action_kwargs)
```

正常情况下 `step.args` 是个 dict，直接当 action_kwargs 传。但有一种特殊情况：`args` 写成了字符串 `"context.response"`，需要从当前任务的对应字段里取出来。如下

```yaml
- id: ask
  type: action
  action: action_response
  args: context.response       # ← 字符串引用,不是 dict
```

代码里 `args.split('.')[1]` 取 `"response"`，再 `state.active_system_task.model_dump(mode="json")`

也就是从当前 `CollectedSystemContext` 里取出 `response` 字段（这正是激活系统流时塞进来的那份 response）。

这种"间接引用"解决了一个实际问题：**同一个系统流如何服务不同的业务场景**。`system_collect_information` 只有一个，但几十个业务 collect step 的问句各不相同——"请告诉我你的订单号""请简单说一下退款原因"。如果把问句写死在系统流的 YAML 里，每个 collect step 都需要一个独立的系统流。通过 `context.response` 间接引用，问句变成了激活系统流时传入的参数，一个系统流通用于所有 collect step。

### 7.3 顺序为什么重要

注意代码里的执行顺序：**先 `_advance_to_next_step` 把 step_id 推到下一步，再构造 ActionCall 返回**。

如果不先推进，外层执行完 action 后内层下一轮还是停在同一个 action step 上，会**死循环**反复执行同一个 action。先推进掉，外层执行完回来时，内层从下一个 step 开始。

<img src="images/09-顺序为什么重要.png" style="zoom: 33%;" />

## 第8章 处理 collect step

`collect` 是四种 step 里最复杂的一个。因为它要处理"槽位的所有可能状态"：刚来时可能本来就有值、可能从聚焦对象自动补一个、可能有值但没通过校验、可能完全没值。

### 8.1 代码

文件：`atguigu/task/flow/executor.py`

```python
def _run_collect_step(self, step: CollectFlowStep, state: DialogueState, flows: FlowsList):

    # 1. 尝试自动补槽
    self._try_to_fill_collect_slot_focused_object(state, step)

    # 2. 判断槽位是否已经填过
    if state.active_task.slots.get(step.slot_name):

        # 填过则直接执行下一步
        self._advance_next_step(state, step)
        return None
    else:
        # 没填过则启动系统过场：补槽任务
        state.start_active_system_task(CollectedSystemContext(
            # flow_id="system_collect_information",
            step_id=flows.get_flow_by_id('system_collect_information').start_step().id,
            slot_name=step.slot_name,
            response=step.response.model_dump(mode="json")
        ))
        return None
```

### 8.2 尝试自动补槽

文件：`atguigu/task/flow/executor.py`

```python
# atguigu/task/flow/executor.py
def _try_to_fill_collect_slot_focused_object(self, state: DialogueState, step: CollectFlowStep):

    if state.focused_object is None:
        return None

    if step.slot_name == 'order_number' and state.focused_object.type == "order":
        state.set_slots({step.slot_name: state.focused_object.id})

    if step.slot_name == "product_id" and state.focused_object.type == "product":
        state.set_slots({step.slot_name: state.focused_object.id})
```

进入 collect step 第一件事：看看 `focused_object` 能不能直接提供这个槽位的值。

**它要解决什么问题？** 考虑一个真实的交互顺序：用户在首页先点了一张订单卡片，然后说"查物流"。这个交互跨了两个 turn：

<img src="images/09-尝试自动补槽.png" style="zoom: 67%;" />

如果在 collect step 不做自动补槽，会发生什么？用户**已经点过订单卡片了**，但进流程后系统还要再问一遍"请告诉我你的订单号"。用户会困惑：我刚不是点了吗？

`_try_to_fill_slot_from_focused_object` 就是为这个场景设计的。它利用 `focused_object` **跨 turn 持久化**的特性（只有 session 过期才清），在 collect step 被处理时顺手检查一下——如果用户的聚焦对象恰好匹配这个槽位，直接填入，省掉一次追问。

## 第9章 小结

### 9.1 这一节实现了什么

| 文件 | 内容 |
| --- | --- |
| `task/flow/executor.py` | `FlowExecutor` 全部方法：`run_task` / `advance_until_action` / `_run_step` / 四个 `_run_*_step` / `_select_next_step` / `_eval_condition` / `_advance_to_next_step` / `_build_action_call` / `_try_to_fill_slot_from_focused_object` |

### 9.2 方法的命名

在 Python 里，方法名前面加一个下划线 `_` 是一个**约定俗成的信号**，意思是"这是内部用的，外人别随便调用"（类似把东西放在家里抽屉里，不在客厅摆着）。

来看看这个类的分工：

| 方法                                                    | 有没有下划线 | 角色                                           |
| ------------------------------------------------------- | ------------ | ---------------------------------------------- |
| `run_task`                                              | ❌ 没有       | **大门**——外部调用流程的唯一入口               |
| `advance_until_action`                                  | ❌ 没有       | **客厅**——流程推进的核心逻辑，对外可见         |
| `_run_step`、`_run_start_step`、`_advance_next_step` 等 | ✅ 有         | **抽屉里的工具**——内部实现细节，外部不需要关心 |

**为什么 `advance_until_action` 不给它加下划线？**

因为它是这个流程引擎里**对外暴露的核心能力**。打个比方：

- `run_task` 就像"启动洗衣机"
- `advance_until_action` 就像"让洗衣机一直走到需要你干预的那一步（比如加洗衣液）"

这两个都是"用户"（外部调用方）需要知道的、有意义的概念。外部代码有可能直接调用 `advance_until_action` 来单独推进流程，所以它属于**公共 API**，不加下划线。

而 `_run_step`、`_advance_next_step`、`_eval_condition` 这些，就像是洗衣机内部的齿轮、电路——拆开来每一步对"用户"没意义，只是实现细节，所以加下划线表示"别碰我，内部用的"。

**总结一句话**：`advance_until_action` 不加下划线是因为它被设计为**对外的公共方法**（和 `run_task` 一样），其他方法加下划线是因为它们只是内部辅助工具，不应该被外部直接调用。

### 9.3 几个值得记住的设计

1. **两层循环**：外层管 action 执行，内层管 step 推进。`action_listen` 是唯一的退出信号。这样拆是因为 step 推进和 action 执行是两种不同粒度的操作——把它们混在一起会让每一步都做类型判断。
2. **系统任务优先**：`current_task()` 优先返回系统任务。用一行 `or` 短路求值，让系统过场和 collect 追问能在业务流程之上"覆盖"执行，不需要中断/恢复框架。
3. **action step 先推进再返回**：先改 `step_id` 再构造 `ActionCall`。顺序如果反过来，外层执行完 action 后内层会停在同一个 action step 上，产生死循环。
5. **自动补槽**：进 collect step 第一件事尝试从 `focused_object` 填。解决的是"用户先点卡片、后说意图"的跨 turn 场景——focused_object 跨 turn 持久化，collect step 主动检查它。

### 9.4 至此整个 task 轨道完整了

回看整条链路：

<img src="images/09-整条链路.png" style="zoom:67%;" />

从用户说"我要退款"到客服回出"请告诉我你的订单号"，所有齿轮都对上了。（整个客服系统的核心逻辑业务任务处理器实现完毕）下一节就可以把KnowledgeHandler、以及ChitChatHandler两个简单的轨道处理器接入进来，形成完整闭环。

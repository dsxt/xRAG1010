# 阿里大模型应用Java开发外包技术面试一轮过

> 根据面试截图整理。题干来自原帖，参考答案用于复习，不是候选人当时的口述。

## 一面

**现场记录**

原帖复盘把技术题分成五段（题号从 CompletableFuture 起，不含自我介绍和反问）：异步处理与容错，微服务组件集成，对话存储和缓存，大模型场景（服务编排、流式响应、提示词、限流），以及项目实战和工程化。整体由基础到进阶，覆盖 Java 调用大模型的主链路。

### 1. 自我介绍

**答题思路**

控制在一到两分钟，按「现在做什么 → 技术栈 → 一个项目钩子 → 和岗位的关系」讲。年限用自己的，不编公司、职级和项目名。

1. 第一句只说自己做 Java 后端多久，系统是业务接口还是模型接入。身份按实际说，把你负责的模块和别人的模块分开。
2. 技术栈按这个岗位排：Spring、MySQL、Redis、HTTP 客户端，然后才是你真写过的模型调用、提示词、流式或限流。只调通过聊天接口，就说调用。
3. 项目只留一个钩子：谁在用，请求从哪进来，你负责哪一段。难点留给后面的并发和工程题。
4. 收在匹配点：能把模型放进已有 Java 服务，并把超时、鉴权、重试和费用收住。
5. 讲完停住。期望薪资不在自我介绍里说，框架名词也不要铺开。

### 2. 在开发中，常用 CompletableFuture 处理异步任务，它与传统 Future 相比优势在哪？如何编排多个模型 API 调用任务？

**参考答案**

结论：`Future` 只能阻塞取一个结果；`CompletableFuture` 能把完成回调、依赖和异常留在同一条链上，适合编排多次模型调用。

`Future.get` 会占住调用线程，任务之间的先后要自己用计数器或线程池拼。`CompletableFuture` 用 `thenApply` 处理同步结果，用 `thenCompose` 接下一跳返回 future 的调用；`thenCombine`、`allOf`、`anyOf` 做汇合，`exceptionally` 和 `handle` 处理异常。Java 9 起可以用 `orTimeout` 给单次调用加超时。

互不依赖的检索用 `supplyAsync` 加 `allOf`。意图识别的结果决定要不要问答时，用 `thenCompose`。模型调用是阻塞 IO，放进有界的专用线程池；`supplyAsync` 不传线程池时走公共 `ForkJoinPool`，池被占满后，进程里其他异步任务一起变慢。

容易漏的是：`allOf` 的结果是 `void`，取值要回到原来的各个 future；`anyOf` 有一个成功后，其余调用不会自动取消，费用还在计。

### 3. 设计提示词缓存时，如何选择 HashMap 和 ConcurrentHashMap？请说明两者的线程安全实现差异。

**参考答案**

结论：会跨请求读写的提示词缓存用 `ConcurrentHashMap`；只在当前线程里组装、安全发布后只读的用 `HashMap`。两者都没有过期和容量上限。

`HashMap` 没有并发控制。JDK 7 并发扩容时头插可能把链表收成环，读线程死循环；JDK 8 改为尾插，这个环基本消失，但并发写仍会丢更新，`size` 也不准。迭代器的 fail-fast 用来发现同一线程里的结构修改，不是并发安全手段。

`ConcurrentHashMap` 不允许 null 键值。JDK 7 按段加锁；JDK 8 起空桶用 CAS，冲突时用 `synchronized` 锁桶头节点，读路径不加锁。计数用 `baseCount` 加 `CounterCell`，避免为了 `size` 锁整张表。`putIfAbsent` 和 `compute` 是原子的，迭代器弱一致。

提示词种类多，只放这个 Map 会一直涨。过期、条数上限和命中率交给有淘汰策略的缓存；共享读写时，线程安全仍然要 ConcurrentHashMap 这一路，而不是退回 `HashMap`。

### 4. 针对不稳定的模型 API 调用，如何实现带指数退避机制的异步重试工具？请简述关键代码逻辑。

**参考答案**

结论：只对超时、连接失败和 408、429、500、502、503、504 重试；等待用满抖动的指数退避，延迟放在调度线程上，不占用正在处理请求的线程。

第 `n` 次失败后的上限是 `min(maxDelay, base * 2^(n-1))`，实际等待在 `0` 到这个上限之间随机，避免同一批调用一起重试。响应里的 `Retry-After` 更长时照它等；长过自己允许的最大等待就放弃。401 先换令牌，不按退避猛打。400、403 和内容审核失败不重试。

每次调用包一层 future 做超时，超时打在包装 future 上，再 `cancel` 供应商返回的那个 future。`CompletableFuture.cancel` 本身不会打断已经阻塞的 HTTP，取消必须落到客户端自己的 future 或订阅上。已经向前端吐过字，或者工具已经改了业务数据，就不再整段重试。

```java
import java.net.ConnectException;
import java.net.SocketTimeoutException;
import java.time.Duration;
import java.util.Objects;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.Supplier;

public final class ModelCallRetry {
    private final ScheduledExecutorService scheduler;
    private final int maxAttempts;
    private final long baseDelayMillis;
    private final long maxDelayMillis;
    private final long attemptTimeoutMillis;

    public ModelCallRetry(ScheduledExecutorService scheduler, int maxAttempts,
                          Duration baseDelay, Duration maxDelay, Duration attemptTimeout) {
        if (maxAttempts < 1) {
            throw new IllegalArgumentException("maxAttempts");
        }
        this.scheduler = Objects.requireNonNull(scheduler, "scheduler");
        this.maxAttempts = maxAttempts;
        this.baseDelayMillis = Math.max(1L, baseDelay.toMillis());
        this.maxDelayMillis = Math.max(this.baseDelayMillis, maxDelay.toMillis());
        this.attemptTimeoutMillis = Math.max(1L, attemptTimeout.toMillis());
    }

    public <T> CompletableFuture<T> execute(Supplier<CompletableFuture<T>> call) {
        CompletableFuture<T> result = new CompletableFuture<>();
        attempt(call, 1, result);
        return result;
    }

    private <T> void attempt(Supplier<CompletableFuture<T>> call, int attemptNo,
                             CompletableFuture<T> result) {
        if (result.isDone()) {
            return;
        }
        final CompletableFuture<T> http;
        try {
            http = Objects.requireNonNull(call.get(), "call");
        } catch (Throwable error) {
            onFailure(call, attemptNo, result, error);
            return;
        }
        CompletableFuture<T> timed = new CompletableFuture<>();
        http.whenComplete((value, error) -> {
            if (error == null) {
                timed.complete(value);
            } else {
                timed.completeExceptionally(unwrap(error));
            }
        });
        timed.orTimeout(attemptTimeoutMillis, TimeUnit.MILLISECONDS);
        result.whenComplete((value, error) -> {
            if (result.isCancelled()) {
                http.cancel(true);
            }
        });
        timed.whenComplete((value, error) -> {
            if (error == null) {
                result.complete(value);
                return;
            }
            http.cancel(true);
            onFailure(call, attemptNo, result, error);
        });
    }

    private <T> void onFailure(Supplier<CompletableFuture<T>> call, int attemptNo,
                               CompletableFuture<T> result, Throwable error) {
        if (result.isDone()) {
            return;
        }
        Throwable cause = unwrap(error);
        if (cause instanceof InterruptedException) {
            Thread.currentThread().interrupt();
            result.completeExceptionally(cause);
            return;
        }
        if (attemptNo >= maxAttempts || !isRetryable(cause)) {
            result.completeExceptionally(cause);
            return;
        }
        long delay = delayMillis(cause, attemptNo);
        if (delay < 0) {
            result.completeExceptionally(cause);
            return;
        }
        try {
            ScheduledFuture<?> wait = scheduler.schedule(
                    () -> attempt(call, attemptNo + 1, result), delay, TimeUnit.MILLISECONDS);
            result.whenComplete((value, done) -> {
                if (result.isCancelled()) {
                    wait.cancel(false);
                }
            });
        } catch (RejectedExecutionException rejected) {
            result.completeExceptionally(cause);
        }
    }

    private long delayMillis(Throwable cause, int attemptNo) {
        long backoff = fullJitter(attemptNo);
        if (cause instanceof ModelHttpException http && http.retryAfterMillis != null) {
            if (http.retryAfterMillis > maxDelayMillis) {
                return -1L;
            }
            return Math.max(backoff, http.retryAfterMillis);
        }
        return backoff;
    }

    private long fullJitter(int attemptNo) {
        long cap = baseDelayMillis;
        for (int i = 1; i < attemptNo; i++) {
            if (cap > maxDelayMillis / 2) {
                cap = maxDelayMillis;
                break;
            }
            cap *= 2;
        }
        cap = Math.min(Math.max(cap, 1L), maxDelayMillis);
        return ThreadLocalRandom.current().nextLong(cap);
    }

    static boolean isRetryable(Throwable error) {
        if (error instanceof ModelHttpException http) {
            int status = http.status;
            return status == 408 || status == 429 || status == 500
                    || status == 502 || status == 503 || status == 504;
        }
        return error instanceof TimeoutException
                || error instanceof ConnectException
                || error instanceof SocketTimeoutException;
    }

    static Throwable unwrap(Throwable error) {
        Throwable cause = error;
        while (cause.getCause() != null
                && (cause instanceof CompletionException || cause instanceof ExecutionException)) {
            cause = cause.getCause();
        }
        return cause;
    }

    static final class ModelHttpException extends RuntimeException {
        final int status;
        final Long retryAfterMillis;

        ModelHttpException(int status, Long retryAfterMillis) {
            super("HTTP " + status);
            this.status = status;
            this.retryAfterMillis = retryAfterMillis;
        }
    }
}
```

调度器只负责延迟。阻塞的 HTTP 要在 `call` 返回的 future 里执行，否则会把调度线程堵住，后面的退避都排不上。

### 5. 在微服务架构下，Spring Boot 如何自动配置 RestTemplate 来调用大模型服务？

**参考答案**

结论：Spring Boot 自动配置的是 `RestTemplateBuilder` 和 `RestTemplateCustomizer`，容器里默认没有 `RestTemplate` Bean。调用大模型时用 Builder 自己声明，构造好的实例可以在多线程间共用。

连接超时设短，读取超时按一次生成的上限单独放宽，两个都要写上。不设读取超时，慢模型会一直占着调用线程。根地址、密钥走配置，`Authorization` 放在 `ClientHttpRequestInterceptor`，调用点不再自己拼头。

`spring-boot-starter-web` 默认不带 Apache HttpClient。较早的 Builder 默认用没有连接池的 `HttpURLConnection`，较新的版本才按 classpath 探测已有客户端。并行打模型时显式换成 HttpClient 5 或 JDK `HttpClient`，并限制总连接和每个路由的连接。多个供应商用多个 Bean，用 `@Qualifier` 分开超时和密钥。

容易漏的是：自己 `new RestTemplate()` 吃不到 Builder 上的超时和定制；连接池耗尽时的等待也要有上限。流式响应不要用它把正文缓冲成完整 `String`。

### 6. 如何用 Spring AOP 统一实现模型 API 的鉴权逻辑？与拦截器方案相比有何优势？

**参考答案**

结论：能不能调、用哪把密钥，放在 AOP；写出站 HTTP 头，放在客户端拦截器。AOP 的优势是能读注解和参数，并且不限于 MVC 入口。

访问模型的方法加注解，切面按供应商和租户从配置取出密钥。`HandlerInterceptor` 只能拦进 `DispatcherServlet` 的请求，看得到 `HttpServletRequest`，看不到方法参数，也拦不住消息消费和定时任务。`ClientHttpRequestInterceptor` 能给同一个 `RestTemplate` 的每次请求补 `Authorization`，多个模型共用一个客户端时，它只能靠 URL 判断用哪把密钥。

配法是切面做准入和选钥，拦截器写头。这样消息、定时任务和 HTTP 入口共用一套决策，从该客户端出去的请求也不会漏头。

容易漏的是：同类内部自调用不走代理，切面不生效。密钥若放在 `ThreadLocal`，异步换线程会丢，还要在 `finally` 里清掉，避免线程池复用时串租户。

### 7. 管理模型服务配置时，如何使用 @ConfigurationProperties 集中管理端点地址和密钥？这种方案有什么好处？

**参考答案**

结论：用一个 `@ConfigurationProperties` 对象接下端点、密钥和超时，启动时校验；缺配置就启动失败，而不是第一次调用才空指针。

前缀例如 `llm`，下面用 Map 表示多个供应商，每个供应商有 `baseUrl`、`apiKey` 和读取超时。类上加 `@Validated`，字段加 `@NotBlank`，再用 `@EnableConfigurationProperties` 或 `@ConfigurationPropertiesScan` 注册。类路径上要有校验实现，否则这些注解不会生效。密钥走环境变量，靠 relaxed binding 进同一个对象，不写进仓库。

和散落的 `@Value` 比，这一组配置类型是明确的，测试可以直接构造对象，profile 之间也能整组替换。调用方只注入这一个对象，端点和密钥不会在客户端里各写一份。

容易漏的是：对象的 `toString` 和启动日志会把密钥打出来，密钥字段不要放进 `toString`。校验只保证配置存在，运行时轮换密钥还要能刷新这个对象，不能在客户端里把字符串再拷一份永久缓存。

### 8. 设计对话记录表时，超长文本字段适合用什么数据类型？如何建立索引优化查询效率？

**参考答案**

结论：一条消息一行，正文用 `MEDIUMTEXT` 和 `utf8mb4`。索引建在会话和顺序上，正文不做 B+Tree 索引。

`TEXT` 大约 64KB，长回复加工具报文容易超。`MEDIUMTEXT` 大约 16MB，日常对话够用。`LONGTEXT` 只是把上限放到 4GB，查询能力没有增加。`VARCHAR` 受行长度限制，不适合当正文。`utf8mb4` 才能存补充字符。

大字段在 InnoDB 里放溢出页，二级索引只能做很短的前缀，既搜不到正文，又拖慢写入。消息表用 `(conversation_id, id)` 做主键，同一会话的行放在一起。翻页写 `conversation_id = ? AND id > ? ORDER BY id LIMIT ?`，不用大 offset。列表不查正文，点开再按主键取。会话列表另建 `(user_id, updated_at)`。

要按正文搜，用全文索引并给中文配 ngram，或放到搜索引擎。容易漏的是：把超长字段加进普通索引不会让历史查询变快。

### 9. 为降低 API 调用成本，如何设计 Redis 缓存键以保证不同用户提示词的隔离性？

**参考答案**

结论：用户标识写进键里，提示词用规范化请求的摘要，才能既复用结果，又不把 A 的回答命中给 B。

键用 `llm:cache:{userId}:{model}:{templateVersion}:{sha256}`。摘要覆盖系统提示、历史、当前问题、温度、工具定义和知识库版本，字段顺序固定。多租户再加租户。隔离放在键上，只写在 value 里挡不住别的用户用同一句话来查。

温度不为 0 时，同一次提示词的答案本来就不同，缓存会改变产品语义，这种请求不放进结果缓存。键设置 TTL。模型和提示词升级就换版本，旧键自己失效。要按用户批量删除时，把用户标识放进 `{}`，让这个用户的键落在同一个槽。

容易漏的是：漏掉工具或知识库版本，会把旧检索结果当成新答案。缓存和登录态分开，避免为了腾内存把会话清掉。

### 10. 设计智能客服流程时，如何实现「意图识别」和「专业问答」服务的流程编排与熔断保护？

**参考答案**

结论：编排层先意图、后问答，两个下游各用各的超时和熔断，问答失败不要把意图的熔断一起打开。

请求进编排服务。意图识别只产出标签和置信度，低于门槛就澄清或走兜底，不进入专业问答。置信度够再按意图选知识库和问答。两步有依赖，顺序调用；同一意图下的多路检索可以并行，汇合后再生成。两步用普通服务代码即可，不必先上流程引擎。

熔断按资源拆开。模型调用必须先有读超时，熔断统计的是已经结束的调用；请求一直挂着，慢调用比例不会涨，开关也不会打开。统计窗口里慢调用或异常比例超过门槛，并且请求数达到最小样本，才打开。打开后意图侧降级成规则，问答侧返回固定话术或只出检索片段。冷却后再放探测，成功才关上。每次尝试单独进入熔断资源，重试次数要少；熔断打开后下一次尝试直接降级。阻断异常不要按模型失败去做退避，打开期间它进不了模型，重试只会把请求拖住。两个资源的并发上限分开，意图占满线程时问答还有额度。

容易漏的是：意图判错会检索错库，熔断解决的是故障，不是误判。流式问答已经吐字之后，不要整段重试。

### 11. 针对模型流式响应，如何通过 SSE 将数据实时推送到前端？请描述技术实现路径。

**参考答案**

结论：后端用流式客户端读模型增量，再以 `text/event-stream` 写给浏览器；浏览器一断开就取消上游，避免空转计费。

1. 接口的 `Content-Type` 用 `text/event-stream`，并带 `Cache-Control: no-cache`。Spring MVC 返回 `SseEmitter`，WebFlux 返回 `Flux<ServerSentEvent>`。`SseEmitter` 先返回给容器，再在别的线程里写；写完之前不要占着容器线程把流读完。
2. 读上游用 WebClient 或 JDK `HttpClient` 的流，按块解析供应商的增量。`RestTemplate` 默认把正文收成一个字符串，不适合这条路径。
3. 每个增量写成 `data:` 帧，帧之间空行分隔。生成期间周期性写注释行做心跳，避免网关按空闲把连接掐掉。结束标记到了再 `complete`。
4. 反向代理关掉这一路的缓冲和压缩，或让响应带 `X-Accel-Buffering: no`，否则 token 会攒一批才到浏览器。异步超时要长于一次完整生成。
5. `send` 抛出 IO 异常，或 WebFlux 收到 cancel，就取消上游订阅。错误用一条错误事件告诉前端，再结束连接。

浏览器自带的 `EventSource` 不能加自定义鉴权头，生产里用 `fetch` 读流，或换短时票据。已经推出去的半段不能整段重试。

### 12. 使用模板引擎处理 Prompt 变量注入时，如何避免字符串拼接导致的性能问题？

**参考答案**

结论：模板按版本编译一次，渲染进同一个缓冲区；历史作为消息列表交给模型，不要先拼成一个越来越大的字符串。

一条表达式里的 `+` 在 Java 9 之后会收成一次拼接，但循环里的 `s = s + piece` 每次都复制，历史越长越接近平方开销。用引擎再对结果循环 `replace`，每次也复制整段提示词。

Mustache、`StringSubstitutor` 这类只做替换、不执行表达式，适合提示词。编译结果按模板版本缓存，请求里只绑定变量，写入一个 `StringBuilder`。少样本示例用模板自己的循环追加，不要在 Java 里先把例子拼成字符串再塞进模板。用户能改模板时，不用会执行表达式的引擎。缺变量就当次失败，避免静默变成空串把提示词改掉。

和一次模型调用比，渲染通常不是瓶颈。容易漏的是：循环拼接超长历史会先把内存打满；工具列表或温度变了还复用上一次渲染结果，省下的是 CPU，错的是答案。

### 13. 如何设计限流策略来控制模型 API 的调用成本？请结合 Sentinel 说明具体方案。

**参考答案**

结论：Sentinel 限制瞬时并发，Redis 按用户扣 token 额度。只限入口 QPS，控不住账单，也挡不住绕过控制器的调用。

在真正发起模型 HTTP 的那条线程里 `SphU.entry`，按模型和接口拆资源。阻塞调用用并发线程数做上限，超限直接拒绝；匀速排队会占着调用线程，对话场景不适合。热点参数用用户或租户，避免一个人打满共享额度。`entry` 和 `exit` 带同一组热点参数。注解切在代理方法上，异步线程要自己 `entry`，自调用同样切不到。

Sentinel 的计数默认在单机内存里，多实例各自计数，总放行上限大约是单机阈值乘以节点数。全局上限用集群流控，或用 Redis 做按天的额度：调用前按输入估算 `INCRBY`，超过就 `DECRBY` 退回并拒绝；成功后按响应里的 `usage` 多退少补。超时又拿不到 `usage` 时保留预扣，已经生成的费用不要抹掉。另配熔断，供应商连续超时或 5xx 时停止继续打。

容易漏的是：预扣和实扣必须原子，失败且确认供应商没计费时要把预扣退回，否则错误请求把当天额度扣光。

### 14. 在历史项目中，如何处理过 AI 相关数据的高并发挑战？具体优化措施是什么？

**答题思路**

用自己真正扛过流量的系统。没有模型并发，就讲最接近的写入洪峰或外部 API，并说明哪一段不是模型。

1. 先界定数据是什么：对话落库、调用日志、向量，还是模型请求本身。峰值来自用户突发、批处理回灌，还是失败后的重试。
2. 瓶颈只讲核对过的那一层：慢查询、连接池等待、线程池队列、Redis 延迟或供应商 429。没有监控数字就讲现象，不编 QPS。
3. 优化说两到三条真正改过的，按「改了哪一层 → 怎么验证」讲。可以落到：入口按用户限流、模型调用放进有界队列并快速失败、相同请求缓存、对话异步批量落库、列表不读大字段。没做的不要说成做了。
4. 收在副作用：缓存会让答案变旧，异步会重复写，限流会拒绝一部分请求。追问就停在能对上代码的那一处。

### 15. 从零搭建大模型应用时，如何规划技术选型和迭代节奏？

**参考答案**

结论：先用托管模型 API 打通一条业务闭环，检索、工具和微调按评测结果再加。选型和迭代都围绕数据能不能出域、一次调用要花多少来定。

选型按约束排。数据不能出域，才考虑私有化模型；能出域就用 API，把时间放在应用。应用层沿用 Spring Boot。对话和审计落 MySQL，一条消息一行；配额、缓存放 Redis。确实有知识库再加向量库，量小可以先做关键词检索。主路径先写成明确的校验、检索、生成、落库。工具和分支稳定之后，再把决定不了的那一段交给 Agent。提示词版本、trace 和 token 用量从第一条链路就记。

迭代按垂直切片。第一段只做一个意图，打通流式、超时、鉴权和用量。然后固定一份真实问题的评测集，每次只改提示词、模型、检索中的一样，看正确率、拒答、延迟和单次费用。超过预算就先收这一段。提示词和检索都到平台期，并且有足够标注数据，再考虑微调。密钥、限流和日志脱敏放在对外开放之前。

容易漏的是：没有评测集，迭代就只是在改句子。换模型或提示词版本时同步换缓存键，避免继续命中旧答案。

### 16. 手写实现一个带自动过期机制的 API 令牌缓存容器。

**参考答案**

结论：用 `ConcurrentHashMap` 存令牌和起始时间，读取时懒删除，后台再扫过期项；有效期用 `nanoTime` 的时间差，不受系统时间回拨影响。

供应商给的是绝对过期时间时，先换成剩余有效期再放入，并减去一段提前量，避免边界上用一张即将失效的令牌。提前量已经盖住剩余时间时不放入，并清掉同键旧令牌。容器只负责存取；过期后的刷新要单飞，避免所有请求一起打鉴权接口。

`get` 和 `put` 均摊 O(1)，定时清理 O(n)，空间是当前条目数。清理任务自己吞掉异常，否则调度器会停掉后续清理；进程退出时关掉调度器。

```java
import java.time.Duration;
import java.util.Objects;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;

public final class ExpiringTokenCache<K, V> implements AutoCloseable {
    private static final class Entry<V> {
        final V value;
        final long startNanos;
        final long ttlNanos;

        Entry(V value, long startNanos, long ttlNanos) {
            this.value = value;
            this.startNanos = startNanos;
            this.ttlNanos = ttlNanos;
        }

        boolean expired(long nowNanos) {
            return nowNanos - startNanos >= ttlNanos;
        }
    }

    private final ConcurrentHashMap<K, Entry<V>> map = new ConcurrentHashMap<>();
    private final Duration earlyExpiry;
    private final ScheduledExecutorService cleaner;

    public ExpiringTokenCache(Duration cleanupInterval, Duration earlyExpiry) {
        Objects.requireNonNull(cleanupInterval, "cleanupInterval");
        Objects.requireNonNull(earlyExpiry, "earlyExpiry");
        this.earlyExpiry = earlyExpiry.isNegative() ? Duration.ZERO : earlyExpiry;
        ThreadFactory factory = runnable -> {
            Thread thread = new Thread(runnable, "api-token-cache");
            thread.setDaemon(true);
            return thread;
        };
        this.cleaner = Executors.newSingleThreadScheduledExecutor(factory);
        long periodMillis = Math.max(1L, cleanupInterval.toMillis());
        this.cleaner.scheduleWithFixedDelay(this::purge, periodMillis, periodMillis, TimeUnit.MILLISECONDS);
    }

    public void put(K key, V token, Duration timeToLive) {
        Objects.requireNonNull(key, "key");
        Objects.requireNonNull(token, "token");
        Objects.requireNonNull(timeToLive, "timeToLive");
        Duration effective = timeToLive.minus(earlyExpiry);
        if (effective.isZero() || effective.isNegative()) {
            map.remove(key);
            return;
        }
        map.put(key, new Entry<>(token, System.nanoTime(), toNanos(effective)));
    }

    public V get(K key) {
        for (;;) {
            Entry<V> entry = map.get(key);
            if (entry == null) {
                return null;
            }
            if (!entry.expired(System.nanoTime())) {
                return entry.value;
            }
            if (map.remove(key, entry)) {
                return null;
            }
        }
    }

    private void purge() {
        try {
            long now = System.nanoTime();
            map.entrySet().removeIf(entry -> entry.getValue().expired(now));
        } catch (RuntimeException ignored) {
            // 保留后续清理；单次失败不停止调度
        }
    }

    private static long toNanos(Duration duration) {
        try {
            return duration.toNanos();
        } catch (ArithmeticException overflow) {
            return Long.MAX_VALUE / 4;
        }
    }

    @Override
    public void close() {
        cleaner.shutdownNow();
    }
}
```

### 17. 反问

**答题思路**

技术面的反问用来确认这条链路的边界，问两个就停。薪资留到谈 offer。

1. 负责的范围是业务接口，还是连模型接入、提示词和检索一起。上下游是哪几个系统。
2. 模型走公有云还是内网，对话内容能不能出域。这决定密钥、日志和缓存放在哪一层。
3. 用量和失败现在谁看，有没有按租户的配额。如果没有，这轮问到的限流和重试是不是这个岗位要补上的。

顺着已经聊过的熔断或流式往下问。对方说不清的那个点，回来判断这块能不能接。

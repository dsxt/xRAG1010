# 传统后端学习笔记（Go）

一份 Go 服务从请求进入到数据落地的知识体系。业务数据以 MySQL 为准，Redis 承担缓存、计数和原子扣减，Elasticsearch 承担搜索，消息队列承担异步和削峰，网关承担入口的鉴权和限流，容器和 Kubernetes 负责交付。进程用 Gin 接请求，用 GORM 访问数据库，连接池是标准库的 `database/sql`。运行时的基线是 Go 1.27，Go 1.26 仍在支持窗口内。1.24 的 Swiss Table map、1.25 起按 cgroup 的 CPU limit 设置 `GOMAXPROCS`、1.26 起默认启用的 Green Tea GC，在这条基线上都已经是现状。1.27 起，具体类型上的方法可以再带自己的类型参数。

组件按仍在维护的线来写：MySQL 8.4、Redis 7、Gin、GORM、`go-sql-driver/mysql`、`go-redis` v9、`grpc-go`、`amqp091-go`、RocketMQ 5 的 `rocketmq-clients/golang`、Kafka 的 `franz-go`、NATS 的 `nats.go`、`nacos-sdk-go` v2、OpenTelemetry Go、Prometheus 的 `client_golang`。单机 MySQL 写满、查询又带不上分片键时，分布式 SQL 用 TiDB，驱动仍然是这条 MySQL 驱动。

一条写请求穿过这些层：

1. 连接在 Linux 上完成 TCP 握手。TLS 在 Ingress 结束，`net/http` 收到 HTTP/1.1。每个连接一个 goroutine 在读，每个请求再进处理函数，阻塞在网络上时让出线程。
2. 网关校验令牌、写入请求 id、按用户限流，再转到 Service。
3. Gin 的中间件把请求 id 放进 `context`，参数由绑定和校验器检查。
4. 库存、限购这类读改写在 Redis 里用 Lua 一次做完。
5. 订单行和发件箱行在同一个 MySQL 事务里提交。提交同时落 redo 和 binlog。
6. 事务提交之后，后台循环把发件箱推到队列。消费者建下游单据或更新搜索索引，按业务键幂等。
7. 日志、指标、trace 带同一个请求 id。副本挂了由 kubelet 拉起。Go 的内存软上限和 cgroup 内存耗尽是两种退出。

读请求先看进程内缓存，再看 Redis，最后查库并回填。强一致的读打主库。

下面先走完一次提交和一次读详情。开发流程放在这些机制之后：分支怎么收拢、同一份镜像怎么从预发进生产、出了问题怎么退回上一份。随后十三节对应这条路径上要能讲清的机制：MySQL、Redis 与缓存、计算机网络、操作系统与 Go 运行时、开发框架、消息队列、Docker 与 Kubernetes、微服务、分布式理论、系统设计、可观测性、云平台、安全底线。网络一节里有 gRPC 和 WebSocket/SSE，Kubernetes 一节里有持久卷和控制器，MySQL 一节里有分库分表和 TiDB，消息队列一节里有 JetStream，运行时一节里有泛型，分布式理论一节里有 Paxos。

---

## 一次请求

用两笔请求把浏览器、网关、应用和数据库走完。写请求是用户在页面上提交一笔普通订单。读请求是打开这笔订单的详情。业务数字以 MySQL 为准，Redis 做扣减前的原子判断和详情缓存。这一节定顺序、每一层的责任，以及提交在中途断开时浏览器、Redis、MySQL 各剩什么。锁、复制、连接池和发件箱的机制在后面各节。

### 三层各管一段

| 层 | 跑在哪 | 这次请求里做的事 |
| --- | --- | --- |
| 前端 | 浏览器里的 React / TypeScript | 收集表单，带登录态发出 HTTP，按错误码改界面 |
| 后端 | 网关进程和 Gin 进程 | 鉴权、限流、生成请求 id，按顺序调用 Redis 和 MySQL |
| 数据库 | MySQL、Redis | MySQL 留下订单、库存和发件箱；Redis 留下可重建的计数和缓存 |

一次点击在浏览器里开始，在数据库里结束。中间每一跳带着同一个请求 id，日志才能对上。浏览器只发 HTTP。MySQL 和 Redis 的连接留在后端进程里。

### 写：提交订单

页面上已有商品、数量和收货地址。页面上的库存只是展示，提交时再问一次后端。

```text
浏览器
  → 网关：验令牌、限流、盖上用户 id、传请求 id
  → Gin：校验这张单属于这个用户
  → 主库：幂等行已在就直接返回
  → Redis：Lua 认幂等键并扣减
  → MySQL：同一事务写订单、条件扣库存、发件箱、幂等行
  → 返回订单号
  → 后台循环把发件箱推到队列
  → 消费者更新搜索或创建下游单据
```

#### 前端

点击之后按钮进入提交中。同一笔订单的第二次点击不再发新请求。

请求体是 JSON：商品、数量、地址、一把幂等键。幂等键在这次点击时生成，放进 `sessionStorage`，键名带上这次提交。刷新页面还能用它查结果。只放在按钮组件的内存里，超时之后页面一重建，就只剩再试一次，于是换成新键，变成第二张订单。超时后的重试用同一把。用户明确要再下一笔，才换新的。

请求头带登录得到的令牌。上游已经给出追踪 id 就放进头里。响应里把请求 id 拿回来，失败时显示在页面上，方便对日志。

浏览器自己的超时比网关略长，网关的错误码才能回到页面。页面展示码和一句说明：未登录、被限流、库存不足、重复提交。堆栈留在服务端日志。

成功响应里是订单号和状态，页面跳到详情。超时之后用这把幂等键查一次订单在不在：在，就按成功展示；不在，再让用户重试。这次查询仍走后端。

#### 后端

请求先到网关。网关校验令牌，从令牌取出用户 id，写进往下传的身份。请求体里客户端自己填的用户 id 被盖掉。限流按用户；没有登录态的接口按 IP。请求体超过上限直接拒绝。通过之后把请求 id 往下传。头里没有，Gin 中间件自己生成，放进 `context`。日志、Redis 和数据库操作都带上它。`*gin.Context` 不交给活过这次处理函数的 goroutine，要带走的是 `c.Request.Context()`。

处理函数看这张单本身：绑定和校验器看参数是否齐全、数量是否为正，服务再看地址是否属于这个用户。网关不知道订单属主。

重试先查主库里的幂等行。行已经在，返回那张订单，不再碰 Redis。行不在，才执行 Lua。Lua 认的是这把幂等键，不只是库存数字。

```lua
local held = redis.call('GET', KEYS[2])
if held then
  return held
end
local left = tonumber(redis.call('GET', KEYS[1]) or '-1')
local n = tonumber(ARGV[1])
if left < n then
  return '-1'
end
redis.call('DECRBY', KEYS[1], n)
redis.call('SET', KEYS[2], 'held', 'EX', ARGV[2])
return 'held'
```

`KEYS[1]` 是库存，`KEYS[2]` 是幂等键。已经是 `held`，直接返回，不再减。库存不够返回 `-1`，不占这把键，稍后库存回来还能再试。够了就把「记下这把键」和「减库存」放在同一次执行里。两个并发请求不会都读到「还剩 1」再都减成功。`ARGV[2]` 是占住的秒数，要长于网关超时，盖住服务超时和一次重试。键先于事务结束而消失，下一次重试会再减一截。

返回 `-1` 就报库存不足，MySQL 不打开事务。返回 `held` 才进 GORM 的事务。同一事务里写订单行、库存的条件更新、发件箱和幂等行。库存更新写成：

```sql
UPDATE inventory
SET stock = stock - ?
WHERE id = ? AND stock >= ?
```

`RowsAffected` 为 0，事务回滚。加回用另一段 Lua：键仍是 `held` 才 `INCRBY`，并删掉这把键，避免两次加回。加回用 `context.WithoutCancel` 保住请求 id，再套一个几百毫秒的超时。请求取消不会把这次加回一起取消。加回失败，或进程在加回之前停了，Redis 就少一截；键过期之后的重试会在这个偏少的值上再减。对账用 MySQL 里的库存覆盖 Redis，不在偏少的值上累加。不确定提交成功没有，先查主库幂等行，不先加回。这些行一起提交，或一起没有。

事务提交之后，接口返回「已下单」。搜索索引、通知、下游单据留在事务外面。另一个循环扫发件箱里未发送的行，推到队列，成功再标成已发送。进程在推送前停了，重启会再推，消费者按订单号做幂等。幂等表、发件箱和确认时机在消息队列那一节。

HTTP 调用和 Redis 调用放在这个事务外面。行锁占到提交为止，中间夹一次外部调用，会挡住别人改同一行库存。

秒杀是这条路径的变体，请求里不建单。活动库存预先写进 Redis，限购和扣减放在同一段 Lua 里，成功后把用户、SKU、请求 id 放进队列，消费者按数据库能承受的速度建单。订单表用用户加活动加 SKU，或用请求 id 做唯一键。脚本和拆段在系统设计的秒杀一节。普通下单仍在请求里把订单和发件箱一起提交。

#### 数据库

MySQL 存必须对的数：订单、库存、发件箱。连接从 `database/sql` 的池取，请求结束归还。一个容器一个进程，一份池。总数是副本数 × `MaxOpenConns`，要小于 `max_connections` 的八成，空闲和寿命在 database/sql 那一节。

Redis 存可以重建的数：预扣的库存计数，以及后面会写上的详情缓存。Redis 丢了，用 MySQL 重新灌。MySQL 里没有的订单，就是没有这张单。

提交成功后删掉这张订单详情的 Redis 键。下一次读从 MySQL 回填。删失败就记一条待删，后台再删。键上的 TTL 是最后一道。先删缓存再被旧值回填的竞态，在缓存那一节。

队列里的消费者晚于事务。用户已经看到订单号时，搜索里可能还没有这条。详情页读 MySQL，或读刚从 MySQL 回填的缓存。

### 读：打开订单详情

```text
浏览器 GET /orders/{id}
  → 网关：验令牌、限流
  → Gin：短缓存 → Redis → 主库
  → 用行里的用户 id 对请求头，再返回 JSON
```

#### 前端

详情页用订单号发 GET。登录态和请求 id 与写请求同一套头。GET 可以安全重试。页面先保留上一份已经拿到的数据，新的响应到了再替换。未登录去登录页。订单不存在，停在说明页。

#### 后端

网关同样验令牌和限流。处理函数先取出这一行，再用行里的用户 id 对请求头里的用户。对不上就拒绝。订单号被猜中也一样。

读的顺序固定。先看本进程的短缓存，秒级 TTL，用 ristretto。没有再看 Redis，分钟级 TTL。都没有再查主库，查到后回填两级缓存。缓存里放的是行，鉴权在读出之后。同一个订单号同时过期时，`singleflight` 只放一个调用去查库。加载用的截止时间取这批调用里最宽的那个，第一个调用取消会把等待者一起带成失败。这把合并只在本进程里。多个进程同时过期，靠 Redis 锁。

查无此单可以短缓存。这把键只表示这个 id 没有行。不属于这个用户的拒绝不写进这把共享键，下一个用户不会因此看到别人的拒绝。

#### 数据库

这次读到的行来自 InnoDB 已提交的版本。普通 `SELECT` 不加锁。缓存键带业务版本，例如 `order:v3:{id}`。字段结构变了就换前缀，旧键等 TTL 自己消失。

刚提交完跳到详情的这次 GET 打主库。从库还没追上时，读副本会把刚写下的订单说成不存在。之后的普通详情可以接受秒级旧缓存。

每个进程有自己的一份短缓存。订单在别的进程里更新之后，本进程要等本地 TTL 结束才去看 Redis。所以本地用秒，Redis 用分钟。

### 时间与失败

超时从外往里变短。浏览器 3 秒，网关 2.5 秒，服务 2 秒。外层更长，内层的错误码才能回到页面。这两秒里要装下 Lua、借连接和条件更新。加回 Redis 另计一个几百毫秒，不占这 2 秒里已经用完的剩余。数字和重试预算在网络那一节，这里把每一刀切断时三边各剩什么列出来。

| 断在哪 | 用户看见 | Redis | MySQL | 同一把幂等键再来 |
| --- | --- | --- | --- | --- |
| Lua 之前 | 超时或失败 | 未动 | 未动 | 重新扣 |
| Lua 返回 `held`，事务未提交 | 超时 | 已减，键是 `held` | 未提交 | 不再减，事务再做一遍 |
| 事务已提交，响应没回到浏览器 | 超时 | 已减，键仍是 `held` | 订单、库存、发件箱、幂等行都在 | 主库查出这张订单，当成功 |
| 条件更新行数为 0，加回成功 | 库存不足 | 数量加回，键删掉 | 已回滚 | 可以再扣 |
| 行数为 0，加回失败，或进程在加回前停了 | 库存不足或超时 | 少了一截 | 已回滚 | 键还在就不再减；键过期后会再减。对账用 MySQL 的库存覆盖 Redis |
| 提交成功，详情键还没删掉 | 已下单 | 详情可能是旧的 | 已是新行 | 详情读主库 |

限流发生在网关，数据库还没被碰到。库存不足发生在 Redis 的 Lua，或 MySQL 的条件更新。页面对这两处用同一句「库存不足」。重复点击带着同一把幂等键，第二次看到的是已经写下的那张订单。

### 和模型请求的边界

上面是一笔业务读写。这次请求还要调用模型时，网关在鉴权之后把短请求转给 Python 编排，流式结果用 SSE 写回浏览器。长任务立刻返回 `task_id`，页面改为轮询或订阅进度。顺序、错误码和取消见 [请求全流程](../agent架构/01-请求全流程.md)。

订单和库存的落库方式不变。模型只提出下一步。扣库存仍由这段确定性代码执行。

---

## 1. MySQL

业务行存在 InnoDB 里。驱动负责连接、事务和 SQL。应用侧的入口是 GORM，底层池是 `database/sql`，驱动是 `go-sql-driver/mysql`。连接串形如 `user:pass@tcp(mysql:3306)/app?charset=utf8mb4&parseTime=true&loc=UTC&timeout=200ms&readTimeout=2s&writeTimeout=2s&rejectReadOnly=true`。MySQL 8 的库、表、连接都用 `utf8mb4`。名为 `utf8` 的字符集是 `utf8mb3` 的别名，最多三字节，生僻字和 emoji 会在写入时失败。

### 一条写入落在哪

InnoDB 的数据页默认 16KB。Buffer Pool 按页缓存数据和索引。专用机器上缓冲池可以占物理内存的一半到七成；容器里按 memory limit 算，给连接、临时表、操作系统和页缓存留余量。`innodb_buffer_pool_size` 按宿主机内存去设，容器一限制内存就会被 OOM 杀掉。

缓冲池用变种 LRU。新读入的页先放在 old 区（约 3/8，`innodb_old_blocks_pct`），只有停留超过 `innodb_old_blocks_time` 再被访问才进 young 区。全表扫描因此不会把热点页都挤出去。页被改过就成脏页，由后台刷盘。刷盘跟不上时，用户线程会被迫参与刷页，语句延迟抬高。

改一行时 InnoDB 做三件事。把旧版本推进 undo，形成版本链，供 MVCC 和回滚使用。把页的修改记进 redo，按 WAL 先写日志再刷脏页。把这次变更记进 server 层的 binlog，供复制和恢复使用。redo 是物理逻辑日志，崩溃恢复用它把页补到提交后的状态。undo 是逻辑上的旧版本。binlog 是 server 层的逻辑日志。

redo 先写 Log Buffer，再按 `innodb_flush_log_at_trx_commit` 刷盘。`1` 是每次提交都 fsync，提交返回后崩溃不丢这批事务。`2` 是每次提交写到操作系统缓存、大约每秒 fsync。`0` 是大约每秒才写日志。生产用 `1`。这个参数只管 redo。8.0.30 起 redo 容量用 `innodb_redo_log_capacity`，不再靠 `innodb_log_file_size` 乘文件个数。

binlog 刷盘由 `sync_binlog` 控制。`1` 是每次提交都 fsync。和 redo 的 `1` 一起用，一台机器上的已提交事务在进程崩溃后还在。组提交把多个事务的 fsync 合并。

事务提交在引擎和 binlog 之间是内部两阶段提交：

1. InnoDB 把事务标成 prepare，对应的 redo 落盘。
2. 写 binlog 并 fsync。
3. InnoDB 把事务标成 commit。

崩溃发生在 binlog 落盘之前，恢复时回滚这笔事务，从库也不会看到它。崩溃发生在 binlog 已经落盘、引擎 commit 标记还没写完时，恢复时根据 binlog 把引擎提交完成。只开 redo、不开 binlog，主从会分叉。

binlog 格式用 `ROW`，`binlog_row_image=FULL`。ROW 记的是行的前后像，复制和 binlog 订阅都依赖它。STATEMENT 记 SQL 文本，带不确定函数时主从会算出不同结果。`binlog_expire_logs_seconds` 控制保留时长，保留太短会让断线的从库补不上增量。

脏页写回数据文件之前会先写 doublewrite。一页 16KB，磁盘不一定一次写完，写到一半掉电，页就撕开了。redo 要应用在完整的页上。doublewrite 先把这批脏页顺序写到专门区域并 fsync，再写各自的数据页。恢复时页校验失败，就从这块区域把整页拷回来，再应用 redo。`innodb_doublewrite` 默认开着。

二级索引的页不在缓冲池里时，修改可以先记在 change buffer，等这页被读入或后台合并时再合进真正的页。唯一索引要立刻检查唯一性，用不上这条路径。缓冲池已经装得下工作集时，change buffer 帮不上忙。

### 一条语句怎么走

一条 SQL 先过服务器层，再进 InnoDB。

1. 连接器做 TCP 握手和认证。MySQL 8 默认认证插件是 `caching_sha2_password`。连接空闲超过 `wait_timeout` 就被断开。密码和全局权限在连接时确定，改密码对已经连着的会话要重连才生效。
2. 分析器做语法分析和名字解析。语法错在这里返回。预处理展开视图、确认表和列存在。
3. 优化器按代价选索引、连接顺序和是否下推条件。代价来自索引基数和直方图，统计信息过时就会选错。`EXPLAIN` 看到的是这一步的计划，`EXPLAIN ANALYZE`（8.0.18 起）能看到实际行数和耗时。MySQL 8.0 起没有查询缓存，结果缓存放在应用和 Redis。
4. 执行器按计划调用存储引擎。能下推的条件下推到引擎，其余在服务器层过滤。

GORM 和 `database/sql` 的占位符是 `?`。值由驱动绑定，不进 SQL 文本。用 `fmt.Sprintf` 把用户输入拼进字符串再交给 `Raw`，解析器看到的就是攻击者写的语句。

MySQL 的经典模型是一个连接一个线程，`max_connections` 同时限制线程数和内存。每个连接会带排序缓冲、连接缓冲和临时表空间，规格要按连接数预留，不能只看缓冲池。

### 索引

InnoDB 用 B+ 树。非叶子节点只存键，叶子才有记录，相邻叶子串成链表，范围扫描沿着链表走。一棵树通常三到四层。二叉树在有序插入时会退化，红黑树对磁盘页不友好，哈希等值快但做不了范围。

主键索引的叶子是整行，这是聚簇索引。二级索引的叶子是索引列加主键。查完整行要拿主键再查一次聚簇树，这是回表。不建主键时，InnoDB 会找一个非空唯一列，找不到就生成隐藏的行 id。隐藏行 id 是全局分配的，并发插入会争用。主键用自增整数：插入多发生在最右边，页满了分裂出新页。随机主键（UUID v4）往页中间插，造成页分裂和碎片。需要在应用里生成、又希望按时间局部有序时，用雪花 id 或 UUID v7。标准库 `uuid` 从 Go 1.27 起可以生成和解析 UUID。

联合索引按定义顺序排序。第一列相同才比较第二列。查询从左往右用，中间断了，右边的列不能再用来缩小查找范围，但可以留在索引里做过滤。`(name, age, position)` 上，`name = ? AND position = ?` 只用到 `name` 做查找，`position` 在 5.6 之后可以索引下推：存储引擎在索引里顺手把 `position` 比掉，再回表。`Extra` 里是 `Using index condition`。范围条件的右边列同样不能做查找。

覆盖索引是查询的列都在这棵索引里，`Extra` 出现 `Using index`，不再回表。`SELECT *` 加大范围时，优化器经常放弃二级索引，因为每一行都要回表。GORM 的 `Find` 默认选出全部列。列表接口用 `Select` 把列收窄，计划才会回到索引上。`FORCE INDEX` 能逼它走，代价不一定更低。

最左前缀包括三种用法：从第一列开始的等值或范围；第一列的最左字符串前缀，`LIKE 'LiLei%'` 能用，`LIKE '%LiLei'` 不能用这棵索引做查找；第一列等值时，后面的列接着用。索引列上包函数、做计算、隐式类型转换，这列的查找能力就没了。字符串和数字比较时，优化器可能把索引列转成数字，整棵索引都用不上。

`EXPLAIN` 看 `type`、`key`、`key_len`、`rows`、`Extra`。`type` 从好到差大致是 `const`、`eq_ref`、`ref`、`range`、`index`、`ALL`。`const` 是主键或唯一键等值命中一行。`eq_ref` 是连接时被驱动表按唯一键最多一行。`ref` 是非唯一索引等值。`range` 是索引上的范围。`index` 是扫整棵索引的叶子。`ALL` 是全表。`key_len` 看出联合索引用了前几列，计算时把字符集字节数、变长长度和 NULL 标记算进去。`rows` 是估算。`Using filesort` 表示排序没沿索引顺序走。`Using temporary` 表示去重或分组用了临时表。

`id` 相同的是同一层，数字更大的子查询先执行。`select_type` 里 `SIMPLE` 是单表或简单连接，`PRIMARY` 是最外层，`SUBQUERY` 是标量子查询，`DERIVED` 是 `FROM` 里的子查询，会先落成临时表。`filtered` 是估算里还能留下的百分比。要看优化器比较了哪些代价，打开这一会话的 `optimizer_trace`，执行语句，再查 `information_schema.OPTIMIZER_TRACE`。

`key_len` 按字符集算。`utf8mb4` 下一个字符最多 4 字节。`varchar(n)` 还要加长度前缀：这一列的最大字节数不超过 255 时是 1 字节，否则是 2 字节。列允许 NULL 再加 1 字节。8.0 默认 DYNAMIC 行格式，单列索引前缀上限是 3072 字节。更早的资料里 768 字节、一个汉字 3 字节，对应的是 COMPACT 和名为 `utf8` 的 `utf8mb3`。

分页用上一页的游标：`WHERE id < ? ORDER BY id DESC LIMIT 20`。`LIMIT 100000, 20` 会先扫过前面的行再丢掉。GORM 的 `Offset` 就是这种写法。按非主键排序时，先在子查询里只取出排序后的主键，再按主键回表拿整行。

有索引的等值连接多是嵌套循环，被驱动表走索引，小表做驱动。`INNER JOIN` 里谁驱动看 `EXPLAIN`。没索引时，8.0 对大表连接经常改用哈希连接。`IN` 适合外表大、子查询结果小。`EXISTS` 适合外表小。两边连接列都有索引时，优化器常常把它们改写成连接。

`count(*)` 和 `count(1)` 都是数行，InnoDB 会选一棵最窄的索引来扫。`count(列)` 要跳过 NULL。引擎不保存一张表的精确总行数。`SHOW TABLE STATUS` 的 `Rows` 是估算。精确总数如果很贵，就维护在别的存储里，并接受延迟。慢查询打开 `slow_query_log`，阈值收到几百毫秒，再配合 `EXPLAIN`。

### 事务、隔离与锁

ACID 落在不同的机制上。原子性靠 undo。持久性靠 redo 和 binlog 的刷盘。隔离性靠锁和 MVCC。一致性是业务约束，由事务边界、约束和外键一起保住，例如扣款和加款在同一个事务里。

并发现象有四种。脏写是两个事务都按自己读到的旧值去写，后写的覆盖先写的。脏读是读到别人还没提交的修改。不可重复读是别人提交之后，本事务再读同一行，值变了。幻读是同一范围再读，行集合变了。

四个隔离级别：读未提交、读已提交、可重复读、串行化。InnoDB 默认可重复读。读已提交解决脏读。可重复读在快照读上再解决不可重复读，当前读配合临键锁把幻读也挡住。串行化把普通 `SELECT` 变成加共享锁的当前读。

MVCC 用 undo 版本链和 Read View。行上有隐藏的 `trx_id` 和 `roll_pointer`。普通 `SELECT` 是快照读，不加锁。可重复读在事务第一次一致性读时生成 Read View，之后普通 `SELECT` 都用这一份。读已提交每次一致性读都生成新的 Read View。`BEGIN` 本身不生成视图。

Read View 里有四项。`creator_trx_id` 是当前事务自己的 id。`m_ids` 是生成视图时还活跃的事务 id。`min_trx_id` 是其中最小的那个，源码字段是 `m_up_limit_id`，比它小的事务在视图生成前已经提交。`max_trx_id` 是生成视图时系统即将分配的下一个 id，源码字段是 `m_low_limit_id`，大于等于它的写入发生在视图之后。

拿一行的 `trx_id` 做判断，不可见就顺着 `roll_pointer` 找更老的版本：

1. 等于 `creator_trx_id`：自己写的，可见。
2. 小于 `min_trx_id`：视图生成前已提交，可见。
3. 大于等于 `max_trx_id`：视图生成后才开始的事务，不可见。
4. 落在两者之间：在 `m_ids` 里说明生成视图时还没提交，不可见；不在里面说明当时已经提交，可见。

`SELECT ... FOR UPDATE`、`FOR SHARE`、`UPDATE`、`DELETE` 是当前读。它们读最新已提交版本并加锁。可重复读下，当前读对索引记录加临键锁：记录锁加这条索引记录前面的间隙锁，区间左开右闭。间隙锁挡住别人往这个范围里插行。

同一个事务里如果先做了快照读，再做当前读，当前读仍可能看见并修改别人刚刚提交的新行。库存和余额用单条条件更新，不依赖先 `SELECT` 再在 Go 里减。

锁加在索引上。没有合适的索引时，当前读会锁住扫过的大量记录，甚至接近锁整张表。条件更新一定要有能定位到行的索引。

| 锁 | 作用 |
| --- | --- |
| 记录锁 | 锁住一条已有的索引记录 |
| 间隙锁 | 锁住两条记录之间的空隙，挡住插入。间隙锁之间不互斥，和插入意向锁互斥 |
| 临键锁 | 记录锁加它前面的间隙。可重复读下范围当前读的默认锁 |
| 插入意向锁 | `INSERT` 在真正插入前加的间隙锁，用来和已有的间隙锁冲突并等待 |
| 意向锁 | 表级的 IS / IX。事务在加行锁之前先加意向锁，表锁才能知道表上已经有行锁 |
| 元数据锁 | server 层的 MDL。DML 加共享，DDL 要排他。长事务占着 MDL 时，后面的 DDL 排队，再后面的 DML 也会被 DDL 堵住 |

唯一索引上等值当前读，记录存在时退化为记录锁。记录不存在时加间隙锁。非唯一索引的等值仍会锁住这一串等值记录和它们两侧的间隙。

读已提交不加间隙锁。`UPDATE` 和 `DELETE` 还会做半一致读：先看最新提交版本是否符合 `WHERE`，不符合就不锁这行。锁范围小，高并发更新更不容易死锁。代价是同一事务里两次快照读可能看到不同的值。互联网业务里很多写多的库用读已提交，库存和余额用单条条件更新保证正确，需要稳定范围的语句再显式 `FOR UPDATE`。

```go
err := db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
    res := tx.Exec(
        "UPDATE inventory SET stock = stock - 1 WHERE id = ? AND stock > 0",
        skuID,
    )
    if res.Error != nil {
        return res.Error
    }
    if res.RowsAffected == 0 {
        return errStockNotEnough
    }
    return nil
}, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
```

`RowsAffected == 0` 就是没扣成。这一条语句本身是原子的。乐观锁是另一种做法：读出 `version`，更新时带 `WHERE version = ?`，同时把版本加一。适合冲突少的行。冲突很高的热点行，重试会打满数据库，热点要移到前面的 Redis 或队列里。

死锁是两笔事务各自占着对方下一步需要的锁。InnoDB 检测到环之后回滚代价较小的那一笔。锁等待超时是 `innodb_lock_wait_timeout`（默认 50 秒）到了还没拿到锁。MySQL 错误码 1213 是死锁，1205 是锁等待超时。驱动把它们包成 `*mysql.MySQLError`，用 `errors.As` 取出 `Number` 再决定重试。

间隙锁之间不冲突，所以下面这个交错能发生。表上 `id` 是主键，没有 `id = 5` 这一行，隔离级别是可重复读：

```sql
-- T1
BEGIN;
SELECT id FROM t WHERE id = 5 FOR UPDATE;   -- 记录不存在，加间隙锁
-- T2
BEGIN;
SELECT id FROM t WHERE id = 5 FOR UPDATE;   -- 另一把间隙锁，互相不挡
-- T1
INSERT INTO t (id) VALUES (5);               -- 插入意向锁要等 T2 的间隙锁
-- T2
INSERT INTO t (id) VALUES (5);               -- 插入意向锁要等 T1 的间隙锁，成环
```

两条 `SELECT` 都成功。真正插入时两边互相等待。InnoDB 回滚其中一笔，应用看到 1213 后重试整笔事务，设次数上限和抖动。先插入、靠唯一键冲突去重，可以不先做这句 `FOR UPDATE`。范围更新按同一顺序加锁，例如都按主键从小到大。事务里只留必须原子的 SQL。HTTP、Redis 和发消息放在事务外面。行锁会一直占到提交，外部调用一慢，就挡住别人，也占着池里的一条连接。

长事务还有另一条事故链：它占着 MDL 和 undo。undo 回收不掉，历史链表变长，查询要沿着版本链找自己的 Read View。这时先查 `information_schema.innodb_trx`，把跑了几分钟的事务杀掉，再谈加索引。

### 复制与高可用

主库把 binlog 发给从库，从库有 IO 线程接收、SQL 线程重放。异步复制是主库写完自己的日志就返回。主库在这一瞬间宕机，已提交的事务可能不在从库上。

半同步要求至少一个从库确认收到日志，主库才给客户端返回。等待点是 `rpl_semi_sync_source_wait_point`。默认 `AFTER_SYNC`：binlog 已经 fsync，从库确认写进 relay log 之后，主库才在存储引擎里提交并响应客户端。`AFTER_COMMIT` 先在主库引擎提交再等从库，提交之后、确认之前，别的会话已经能读到这行。超时后退回异步。生产保持 `AFTER_SYNC`。故障转移之后按新主重建旧主。

GTID 用 `source_id:transaction_id` 标识每一笔已提交事务。从库按 GTID 集合续传，换主时不用手工对文件名和位点。

从库可以承担读。写之后立刻读自己的写入，要打主库，或者在从库上 `WAIT_FOR_EXECUTED_GTID_SET` 等到这笔 GTID 重放完再读。延迟的常见来源是从库单线程重放、大事务、从库机器更弱、锁等待。并行复制按库或按 writeset 并行。大事务拆开。

GORM 的 `dbresolver` 插件可以按语句把读打到副本。只读标志本身不会把语句送到副本，路由要写在应用里。刚写完的查询继续走主库。`rejectReadOnly=true` 让驱动丢掉已经被切成只读的连接，故障转移后旧主变成从库时，池里的连接不会继续往上写。

组复制（MGR）用 Paxos 在多数节点上提交事务，单主模式下写仍进主节点。它解决的是复制本身的高可用。代理或编排负责把写流量切到新的主。切换期间会有一小段不可写，客户端要重试幂等的语句。

只读实例把 `read_only` 和 `super_read_only` 打开。备份用物理备份加 GTID，逻辑备份适合小库和单表。恢复演练按固定周期做。

### database/sql 与 GORM

`*sql.DB` 可以同时被很多 goroutine 使用，它就是池。`*sql.Tx`、`*sql.Conn`、`*sql.Rows` 不能并发使用。一个请求一个事务，请求结束就提交或回滚。把 `*sql.Tx` 放进全局，会把连接和事务串到下一次请求。

```go
sqlDB, err := gormDB.DB()
sqlDB.SetMaxOpenConns(20)
sqlDB.SetMaxIdleConns(20)
sqlDB.SetConnMaxLifetime(25 * time.Minute)
sqlDB.SetConnMaxIdleTime(5 * time.Minute)
```

`SetMaxOpenConns` 的默认是 0，表示不限制。流量一上来会把 `max_connections` 打满。`SetMaxIdleConns` 的默认是 2。只把最大连接设成 20、空闲仍留 2 时，突发过后多出来的连接被关掉，下一波又重新握手。生产把两者设成同一个数，再用空闲时间把长时间不用的连接收掉。

池子借光之后，后来的 goroutine 堵在借连接上，直到有连接归还，或者它带着的 `context` 被取消。标准库没有单独的「借连接超时」。处理函数如果拿 `context.Background()` 去查库，池空了就会一直等。请求的 `context` 就是这道预算，要短于网关超时。`DB.Stats` 里的 `WaitCount`、`WaitDuration`、`InUse` 做成指标。总数是副本数 × `MaxOpenConns`，要小于 `max_connections` 的八成左右。Go 服务一个容器一个进程，没有多 worker 再乘一次。

`SetConnMaxLifetime` 必须短于数据库 `wait_timeout`、云负载均衡和防火墙的空闲断开时间，并留出几分钟余量。池子不会在借出前主动 ping。空闲连接被对端掐掉之后，下一次使用如果驱动返回 `driver.ErrBadConn`，`database/sql` 会丢掉这条连接再换一条，但只在语句还没真正发出去的时候。已经发出去的语句超时，结果不确定，不能靠换连接再执行一遍。

`parseTime=true` 把 `DATE` 和 `DATETIME` 扫进 `time.Time`。`loc=UTC` 把会话时区固定住，容器的本地时区一变，扫出来的时刻不会跟着漂。`timeout`、`readTimeout`、`writeTimeout` 是驱动自己的拨号和读写截止时间，和 `context` 一起生效，以先到的为准。

占位符默认走预处理。`interpolateParams=true` 改成驱动在客户端转义后拼进 SQL，用在不支持预处理的代理上。业务代码两边都写 `?`，不自己拼值。预处理语句挂在连接上，不同 SQL 太多会顶到 `max_prepared_stmt_count`。GORM 的 `PrepareStmt: true` 会在进程里缓存预处理语句，代理不支持时不要开。

GORM 对单条 `Create`、`Update`、`Delete` 默认再包一层事务：`BEGIN`、语句、`COMMIT`。多一次往返，也多占一会儿连接。初始化时 `SkipDefaultTransaction: true`，单条写入不再隐式开事务；真正要原子的几条语句用 `Transaction` 显式包住。

`*gorm.DB` 初始化之后可以共享。请求里用 `WithContext` 拿出带截止时间的会话，取消才能传到驱动。在全局的 `*gorm.DB` 上改语句状态，会串到别的请求。`Rows` 必须 `Close`，循环之后还要看 `rows.Err()`。

列表接口避免 N+1。`Preload` 是主查询之后按外键一次 `IN`。`Joins` 适合多对一、只要关联表少数几列的情况。循环里对每行 `First` 会把一次列表变成几百次查询。事务里的对象没有身份映射，改了字段要自己 `Save` 或 `Updates`，不会在提交时自动把改过的字段刷出去。

`Find` 找不到行不是错误，拿到的是空切片。`First` 找不到行会返回 `gorm.ErrRecordNotFound`。用 `errors.Is` 判断，不要比较错误字符串。

SQL 本身就是合同、表也不靠关联预加载时，用 sqlc：查询写在 SQL 文件里，生成带 `context` 的函数和具体结构体，参数仍然是 `?`，底层还是同一个 `*sql.DB`。没有 `Preload`，第二次查询就是你写出来的第二条 SQL。N+1 藏不住。迁移仍用 golang-migrate 或 goose，sqlc 不改表。一个服务选一种访问方式。GORM 的事务对象和 sqlc 的生成函数不要混在同一条业务路径上，连接和提交点会分成两套。

迁移用 golang-migrate 或 goose，放在滚动更新之前的一次性 Job 里跑。多个副本同时改同一张表会抢 MDL。8.0 里不少加列可以用 `ALGORITHM=INSTANT`，做不到就让语句失败。改列类型、加二级索引仍可能重建或长时间占用。大表的阻塞变更用 gh-ost、pt-online-schema-change 在线做。变更期间的 binlog 量会上升，从库延迟要一起看。

### 分库分表

先确认单机的索引、SQL、连接数、缓冲池和磁盘已经用尽，再拆。垂直拆按业务分库，订单和用户各自的库。水平拆按用户 id 或订单 id 取模或按范围。

两条接入方式：

- 代理（Vitess、ShardingSphere-Proxy）对外说 MySQL 协议。应用拿普通连接串连代理，分片规则在代理上。
- 应用内分片：按分片键选择一个 `*sql.DB` 或一个 GORM 实例。少一跳网络，分片规则跟着应用发布。分片键不在查询里就要扇出到所有库。

分片键选查询一定会带上的列。按用户 id 拆之后，按订单 id 查就要有一张订单 id 到用户 id 的映射，或者订单 id 里就编进用户 id。范围查询和时间排序会扇出到所有分片，这种查询改走搜索引擎或汇总库。扩容用成倍的分片数，数据只需要搬一半。迁移期间双写或按 id 段搬，对完数再切路由。跨分片的写入用消息做最终一致。

全局唯一约束在单表里由唯一索引保证。分片之后，唯一键必须包含分片键，否则要单独建一张以这个键为分片键的索引表。

### 分布式 SQL

分片键选不出来，或者一次写入经常要改多个分片时，把分片从应用里拿掉，换成一套仍说 MySQL 协议的分布式数据库。这条笔记展开 TiDB：SQL 层是 Go 写的，存储层是 TiKV，应用继续用 `go-sql-driver/mysql` 和 GORM，连接串指向 TiDB 服务器或前面的负载均衡，不在代码里做取模。

| 组件 | 做什么 |
| --- | --- |
| TiDB 服务器 | 无状态。解析、优化、生成分布式执行计划，自己不存行。可以水平加节点 |
| PD | 集群的元数据和调度。分配事务时间戳。至少三节点、奇数个 |
| TiKV | 行数据。一段左闭右开的 key 区间是一个 Region，默认三副本，副本之间走 Raft |
| TiFlash | 列存副本，给分析查询。订单的写入路径不经过它 |

一笔事务的时钟在 PD 上。开始时拿一个 `start_ts`，提交时再拿一个 `commit_ts`。时间戳是一个 int64：高位是物理时间的毫秒，低 18 位是这一毫秒里的逻辑计数，一毫秒最多大约 26 万个。PD 的领导者是这把钟的唯一分配点。每笔事务至少两次问 PD，再加上下面的两阶段提交。同一个机房里这是一小段固定开销；TiDB 和 PD 分在两个地域时，每笔订单都要付这个往返。

存储上的事务是 Percolator 的两阶段提交。TiDB 服务器是协调者。

1. 写入先留在这台 TiDB 的内存里。
2. 预写：变更按 Region 分组，发给各个 Region 的 leader。其中一个 key 是主键（primary）。
3. 主键带着 `commit_ts` 提交成功，事务就已经提交。这时可以给客户端返回成功。
4. 其余 key 的提交是异步的。协调者在这一步之前停了，后来的读取发现锁，就去看主键：主键提交了就把从键提交掉，主键回滚了就把从键清掉。

3.0.8 起，新建集群默认是悲观事务。`SELECT` 是快照读，读 `start_ts` 能看见的版本，不加锁。`UPDATE`、`DELETE`、`INSERT` 和 `SELECT FOR UPDATE` 是当前读：读最新已提交的版本，并在 TiKV 上落下悲观锁，锁要等 Raft 确认。别的事务改同一行会等这把锁。普通的 `SELECT` 走多版本，不会被这把锁堵住。乐观事务把冲突留到提交：预写时发现行在 `start_ts` 之后被别人提交过，整笔回滚。

TiDB 把隔离级别标成 `REPEATABLE READ`，实现是快照隔离。事务开始之后，别的事务提交的版本不会出现在这次快照里。它不拿 InnoDB 那种间隙锁。快照隔离允许写偏斜：两个事务各读各的快照，各自插入，提交时都以为自己没和对方冲突。悲观锁能挡住「两个人同时改同一行」，挡不住「两个人根据同一个快照插入本该互斥的两行」，这种互斥仍要数据库唯一键。同一笔悲观事务里，后面的 `SELECT FOR UPDATE` 看的是当前已提交版本，和开头那次快照读可以不一致。先快照读库存、再 `FOR UPDATE` 扣减，中间要以第二次读到的数为准。

3.0 起事务的自动重试默认关闭。冲突或等锁失败时，应用把整段事务重做，只重发最后一条语句会在一个已经回滚的事务上继续。乐观冲突的错误号是 `9007`。等锁超时会看到和 InnoDB 同类的 `1205`。打开数据库侧的自动重试有机会把隔离打穿，保持关闭。

```go
const attempts = 3

func place(ctx context.Context, db *gorm.DB, in Order) error {
    var err error
    for i := 0; i < attempts; i++ {
        err = db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
            return insertOrder(tx, in)
        })
        if err == nil || !retryable(err) {
            return err
        }
    }
    return err
}
```

`retryable` 只认写冲突和锁等待超时。唯一键冲突、参数错误重做也是同一个结果。

TiKV 可以把「锁还没走完 Raft apply」提前答复给 TiDB，锁的落盘异步完成，用来削掉悲观锁的一段延迟。这个窗口里 Region 的 leader 停了，语句已经往下走，锁可能没落上。业务把「语句返回」当成「多数派已经拿着这把锁」，就会在这个窗口里判错。真正要互斥的不变量仍然落在唯一键或主键提交上。

单行上限 6MB。单笔事务默认不超过 100MB（`txn-total-size-limit`，配置上限可以放到 1TB），TiDB 进程的内存会涨到事务体积的两三倍。批量导入拆成多笔。4.0 之前还有单事务 30 万个键值对的限制，这个条数限制已经去掉，体积限制还在。GORM 默认给单条写入再包一层事务，在这里每一层都是一次 TSO 加一次两阶段提交，`SkipDefaultTransaction` 仍然要开。

自增主键在单个 TiDB 服务器上递增，全局唯一，跨服务器不是一把连续的序列，也不能假设下一号是 `max(id)+1`。连续的主键会打在同一个 Region 的 leader 上，写热点在 Raft 上，不在 InnoDB 的页锁上。把热点拆开用 `BIGINT AUTO_RANDOM`，或者继续用前面的雪花 id。默认的读写都在 Region leader 上。一个 SKU 的库存行无论集群有多少节点，仍然只有一个 leader。秒杀的扣减还是 Redis 里的 Lua，不是把热点行交给 TiDB 就水平扩展了。

外键从 6.6 起支持，8.5 起一般可用。悲观事务里检查父行，相当于对父行做 `SELECT FOR UPDATE`。子表高并发都引用同一父行时，锁会堆在这一行上。订单这种路径的参照完整性放在服务里校验，热表不靠外键串行。

CockroachDB 是另一条分布式 SQL：协议是 PostgreSQL，驱动换成 `pgx`，时钟用混合逻辑钟，默认隔离是可串行化。这套笔记的驱动和 SQL 方言是 MySQL，接入不换协议时走 TiDB。

---

## 2. Redis 与缓存

Redis 在这条体系里做四件事：缓存、计数、分布式锁、原子脚本。客户端用 `github.com/redis/go-redis/v9`。一个 `*redis.Client` 可以同时被很多 goroutine 使用，进程退出时 `Close`。Redis 7 起，小哈希、小有序集合和列表 quicklist 的节点用 listpack。Stream 可以做单机的轻量日志，堆积受内存限制，跨服务的事件总线仍用后面的消息队列。

### 命令执行

一条命令的执行在主线程上排队。快是因为数据在内存里，数据结构把操作控制在可预期的复杂度，加上 epoll 把「连接可读」变成事件。慢命令会堵住后面所有命令：`KEYS`、对大 key 的 `SMEMBERS` 或 `HGETALL`、对大集合的 `SUNION`。`io-threads` 默认是 1，也就是只有主线程。把它设成大于 1 之后，多出来的线程默认只写响应，`io-threads-do-reads yes` 才让它们也读。`io-threads 4` 表示 1 个主线程加 3 个 IO 线程。命令本身仍在主线程执行。`UNLINK` 和过期删除可以丢给后台线程释放内存。

key 在服务器里是 SDS，可以存二进制，长度单独存放。值用 JSON 或纯数字，键名带业务和版本：`order:stock:v1:{sku}`。过期时间在写入时一起设上，`SET key value EX 秒`。先 `SET` 再 `EXPIRE` 的话，两条命令之间进程挂了，键就没有过期时间。

过期有两条清理路径。访问到一个已经到期的键时顺手删掉，这是惰性删除。后台定时随机抽一批带过期时间的键，删掉其中到期的。到期不代表内存立刻下降。内存上限靠 `maxmemory` 和淘汰策略，和过期是两套机制。

go-redis 把多个 goroutine 的命令写进连接池里的连接。Redis 服务端仍是单线程执行。`BLPOP` 这类阻塞命令会占住一条连接，池的大小要按阻塞调用单独留。`Watch` 需要一条独占连接，用 `TxPipelined` 或连接上的事务，不要和别的命令混在从池里借来的短连接上碰运气。

超时要拆开。`DialTimeout` 管握手，`ReadTimeout` 和 `WriteTimeout` 管一条命令的读写。当前 v9 把读写超时的默认从 3 秒改成了 5 秒，重试退避的默认也变过。生产把这些数写进配置，不依赖库的默认。超时后这条命令的结果不确定，服务器可能已经执行了 `INCR`。扣库存靠 Lua 的返回值判断，超时就用请求 id 再查一次。

`MaxRetries` 的默认是 3。字段写成 0 仍然是默认 3，要关掉必须写成 -1。重试发生在超时和连接错误上，非幂等的写会被多执行一次。扣减和发号把重试关掉，读可以留少量重试和抖动。

### 结构

按过期粒度和内存选类型：

| 类型 | 用法 | 编码 |
| --- | --- | --- |
| 字符串 | 计数用 `INCR`，对象用 JSON。每个 key 都能单独过期 | int、embstr、raw |
| 哈希 | 一个对象的多个字段。7.4 起 `HEXPIRE` 可以让单个字段过期，更早只能设在整个 key 上 | 字段少用 listpack，多了转哈希表 |
| 列表 | 队列、时间线 | quicklist，节点是 listpack |
| 集合 | 去重 | 全是整数且数量不大时用 intset，否则哈希表 |
| 有序集合 | 排行榜、延迟任务 | 成员到分数用哈希表，按分数有序用跳表。量很小用 listpack |
| Stream | 带 id 的追加日志，消费组、待确认列表 | listpack 组成的 radix tree |
| 地理位置 | `GEOADD` / `GEOSEARCH` | 分数是 geohash，底层是有序集合 |

每个数据库是一张哈希表。存一个 key，先算哈希，再对桶的个数取模，落到一个桶。两个 key 进同一个桶，用链表串起来。设了过期时间的 key 再在另一张过期表里登记，过期扫描只看这张表。桶上的链太长就扩容。一次性把全部 key 搬到新表，主线程会停住。渐进式 rehash 让新旧两张表同时存在：查找先查旧表，没有再查新表；新写入只进新表；每次增删改查顺手搬一个桶；空闲时定时任务再搬一点。旧表空了，两张表交换，扩容结束。

哈希表里的值包着一个 redisObject。`type` 是对外的类型，`encoding` 是当前真正用的结构。数据少用紧凑结构省内存，数据多了换成能保住复杂度的结构。`OBJECT ENCODING` 能看到现在用的是哪一种。

字符串有三种编码。能放进整数的用 int，数字放在对象里，不再单独申请一块字符串。Redis 7 起，不超过 44 字节的短字符串用 embstr：redisObject 和 SDS 一次分配在连续内存里，改一次就会转成 raw。更长的一开始就是 raw，对象和字符串分两次申请。SDS 在头部记下长度，取长度不用扫描，中间有 `\0` 也不会被截断。追加时预留空间：需要的新长度不到 1MB 就按这个长度翻倍，达到 1MB 之后每次多留 1MB。一个字符串最大 512MB。Bitmap 就是按位使用的字符串：签到用一个用户一年一条，一位一天；在线状态用用户 id 当位的下标，一亿个用户大约 12MB。`BITCOUNT` 数置位的个数，`BITOP AND` 得到连续几天都出现过的用户。

哈希、小集合、小有序集合用 listpack：一块连续内存，头部记下总字节数和元素个数，每个格子记下自己的编码、数据和自己的长度，末尾一个结束标记。它的前任 ziplist 记的是前一个格子的长度。前一个格子变长，后一个用来记长度的字段也可能从 1 字节涨到 5 字节，后面的格子跟着改，这是连锁更新。listpack 只记自己，倒着走时读末尾的长度就能跳过。哈希的字段个数或单个值超过 `hash-max-listpack-entries`、`hash-max-listpack-value`（默认 128 和 64）就转成哈希表。7.4 起 `HEXPIRE` 可以给单个字段设过期，更早的过期只能挂在整个 key 上。

列表用 quicklist：双向链表的每个节点是一个 listpack。`list-max-listpack-size` 默认 -2，一个节点大约 8KB。纯链表每个元素都要前后指针，纯数组在中间插入要搬移后面的元素。quicklist 把元素分进一节节节点，两头进出只动端点上的节点。元素很少时可以只有一个 listpack。

集合里全是整数、个数不超过 `set-max-intset-entries`（默认 512）时用 intset：从小到大排好的整数数组，查找用二分。塞进一个当前编码放不下的数，整个数组从 16 位升到 32 位或 64 位，不再降回去。有非整数但元素不多，用 listpack。再多就换成哈希表，只用键，值空着。

有序集合元素少时仍是 listpack，成员和分数按分数挨着放。超过 `zset-max-listpack-entries` 或 `zset-max-listpack-value`（默认同样是 128 和 64）就换成跳表加哈希表。哈希表按成员查分数。跳表在有序链表上按概率往上加层，每往上一层的概率是 1/4，最高 32 层。查找从最高层往右，走过头就下一层。每一层记着这一跳跨过多少个节点，所以查排名也是对数复杂度。按分数取一段时，先找到起点，再沿着最底层往后走。插入和删除只改几根相邻指针。红黑树做范围扫描要中序走树，插入删除还要旋转和变色，跳表把这两件事都留在链表上。

HyperLogLog 只估不重复的个数，不存元素本身，固定大约 12KB，标准误差大约 0.81%。它能给出「有多少个不同的人」，给不出成员名单。地理位置把经纬度编成 geohash，当作分数放进有序集合。同一片网格里的点分数接近；贴着网格边界的近邻，分数不一定挨在一起，`GEOSEARCH` 会把相邻网格一起算进去。Stream 比把列表当队列多了消费组和待确认列表：消息被取走但没确认，就留在这个列表里，消费者停了可以由别人认领。它仍然受这台 Redis 的内存限制，跨服务、要落盘的事件走消息队列。布隆过滤器是一条很长的位数组加上几个哈希函数。写入时把算出的几位置成 1。查询时只要有一位还是 0，这个元素一定不存在；全是 1 只说明可能存在，这几位也可能是别的元素置上的。元素只能加，不能单独删。Redis 8 起开源发行版内置 `BF.ADD` 和 `BF.EXISTS`，更早用 RedisBloom 模块，或者用 Bitmap 自己做。

管道把多条命令一次写出。`Pipeline` 不包 `MULTI`/`EXEC`，某一条失败，其他命令仍然执行。`TxPipelined` 才包事务。Redis 事务没有回滚。管道和事务都做不到「先读再按结果写」的原子。这种读改写用 Lua。`Watch` 加上事务是乐观锁，写之前键变了就整事务失败，由客户端重试。Lua 在主线程上原子执行，脚本里不要写循环去扫大集合。

Redis 7 的 Function 把脚本登记在服务器上，随复制持久化。`Eval` 的脚本正文跟应用版本走。无论哪一种，脚本要短，key 从 `KEYS` 传入，这样集群才能检查槽位。

### 连接、内存与超时

`PoolSize` 的默认是 `10 * GOMAXPROCS`。它是池里连接的基线，不是硬顶。`MaxActiveConns` 默认 0，忙的时候还会超出 `PoolSize` 继续建连接。集群客户端对每个节点各有一份池，总连接约是节点数乘以单节点上限，再乘副本数。这个积要小于 `maxclients` 的八成。`PoolTimeout` 默认是读超时再加 1 秒，池空了等到这个时间就返回错误，调用方要把它和「键不存在」分开。

```go
rdb := redis.NewClient(&redis.Options{
    Addr:         "redis:6379",
    DialTimeout:  200 * time.Millisecond,
    ReadTimeout:  200 * time.Millisecond,
    WriteTimeout: 200 * time.Millisecond,
    PoolSize:     20,
    MaxActiveConns: 20,
    PoolTimeout:  200 * time.Millisecond,
    ConnMaxIdleTime: 5 * time.Minute,
    MaxRetries:   -1,
})
```

大键用 `MEMORY USAGE` 或 `redis-cli --bigkeys` 找。删除大键用 `UNLINK`。线上扫键用 `Scan`，游标由服务端保持，中间有写入时不保证不重复、不遗漏，调用方自己去重。`maxmemory` 设了之后要选淘汰策略：

| 策略 | 行为 |
| --- | --- |
| `allkeys-lru` | 在所有键里淘汰最近最少使用的 |
| `allkeys-lfu` | 在所有键里淘汰使用频率最低的，计数会随时间衰减 |
| `volatile-ttl` | 只在设了过期时间的键里淘汰更快过期的 |
| `volatile-lru` / `volatile-lfu` | 只淘汰设了过期的键 |
| `noeviction` | 写满后拒绝写入 |

缓存用 `allkeys-lfu` 或 `allkeys-lru`。访问分布很斜时 LFU 更合适。`volatile-*` 只在带 TTL 的键里挑，如果这些键的内存远小于 `maxmemory`，内存仍会顶满，然后写失败。`noeviction` 适合把 Redis 当锁和计数的存储、不能丢键的场景。拒绝写入时，应用要把 Redis 故障和「键不存在」区分开，否则会把一次内存满当成缓存未命中，全部打到数据库。

`MONITOR` 会把每条命令打出来，生产上会把主线程拖慢。热 key 在代理或业务侧对 key 打点。

集群客户端收到 `MOVED` 会更新槽表并跳到持有该槽的节点，`ASK` 只对这一次请求临时跳转。新版本把集群状态的周期刷新默认拉长到了 60 秒，更早是 10 秒。主从切换之后、下一条重定向到来之前，连接可能还指着旧主。刷新间隔显式写成 10 到 30 秒这一档，地址不写死成某一台。

### 持久化、复制、哨兵、集群

RDB 是某一时刻的全量二进制，恢复快，生成慢。`bgsave` 用 fork 和写时复制，父进程继续服务。写得多时，拷贝被改过的页，父进程内存会涨上去。AOF 追加命令，`everysec` 大约每秒 fsync，最多丢一秒。`always` 每条命令都 fsync。重写把一串 `INCR` 收成一条 `SET`。4.0 起混合持久化在重写时写成 RDB 头，之后的新命令仍追加 AOF。Redis 7 的 AOF 可以拆成多个文件。缓存可以只开 RDB，甚至不持久化，数据能从数据库重建。锁和限购如果只活在 Redis 里，就要开 AOF，并接受最多大约一秒的丢失窗口。

主从第一次同步是全量 RDB，加上复制缓冲区里的增量。断线后用 backlog 里的 offset 续传，offset 已经被覆盖就退回全量。所有从节点同时找主做全量，会把主打满。从节点下面再挂从节点。Redis 的复制是异步的。

哨兵监视主节点。quorum 是多少个哨兵同意才算主失效，一般是哨兵数的一半加一。真正执行故障转移还要有多数哨兵可达。客户端用 `NewFailoverClient`，地址写哨兵，不写死某一台主。故障切换有几秒到十几秒不可写。从节点复制有延迟。扣减、锁、限购走主节点。

Cluster 把数据分成 16384 个槽，`CRC16(key) % 16384`。跨槽的多 key 命令会被拒绝，除非用哈希标签 `{sku}` 让相关 key 落在同一槽。事务、管道和 Lua 里的多个 key 也受这个限制。节点用 gossip 交换状态。某个槽的主从都不可用时，这个槽上的键不可用；`cluster-require-full-coverage` 为 yes 时，整个集群拒绝写入。

### 锁

单机上加锁是一条命令：`SET key token NX PX 毫秒`。token 是随机值。`SetNX` 的过期时间必须大于 0，才会带上 TTL。传 0 时命令不设过期，进程死后锁还在。释放时只有值仍是自己的 token 才 `DEL`，用一段 Lua 比较再删。两条命令拆开做，会把别人刚拿到的锁删掉。

```go
ok, err := rdb.SetNX(ctx, key, token, 10*time.Second).Result()
if err != nil || !ok {
    return errBusy
}
defer rdb.Eval(ctx, unlockScript, []string{key}, token)
```

```lua
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
end
return 0
```

业务必须在 TTL 之前结束。需要续期时，用同一段「先比对 token 再 `PEXPIRE`」的 Lua，由持有者的 goroutine 周期调用。进程被杀死后续期也停了，锁会在 TTL 后释放。`context` 取消不能自动释放锁，释放放在 `defer` 里，并且用一个不受请求取消影响的短 context，避免请求一断就把 Lua 也取消掉、锁一直留到 TTL。

业务耗时和锁超时重叠时，两个人可能都以为自己持有锁，所以临界区再检查一次资源状态。主从切换时锁可能丢：写在主上，还没复制到从，主挂了，新主没有这把锁。看门狗用的是 Redis 服务器的过期时间，客户端时钟跳了不会直接让这把锁提前过期。多主投票的 Redlock 要求多个独立主节点、时钟偏差有上界、实例会在超时后放锁。网络延迟和时钟跳变会破坏这些假设。

只比对 token 也挡不住「锁过期之后，原持有者还在往下写」。它醒过来时锁已经属于别人，它仍把后面的数据库更新发出去。资源侧要再认一个只增不减的 fencing token：每次成功加锁发一个更大的序号，写入时带上，序号更小的写入直接拒绝。etcd 选主里的 revision 就是这种序号。数据库行上的版本号也能当。库存扣减用 Lua 直接改数字。锁只包住「从数据库装载库存进缓存」这种重建过程。真正不能丢的互斥，用数据库唯一键或行锁。

### 缓存

详情页拆成能缓存的部分和必须实时的部分。标题、图、属性、规格可以缓存。价格、库存、促销单独接口，缓存时间短，或者直接读 Redis。扣库存和支付结果以数据库事务为准。

整页静态化在发布时渲染成 HTML，放到对象存储或 Nginx 能直接读的目录。模板一改要重渲染全部商品。商品一多就改成 Nginx 缓存 HTML 片段，实时数据走接口。带登录态的页面按用户片段缓存，避免把一个人的页面交给下一个人。

本地缓存挡在 Redis 前面。进程内用 ristretto 这类带准入的缓存，设最大条数和 TTL。每个进程有自己的一份，失效不会自动广播。本地 TTL 用秒级，Redis TTL 用分钟级。没有失效通知时就靠短 TTL。要主动失效，就在更新后发一条 Redis Pub/Sub；Pub/Sub 不留存，进程刚启动时本地是空的，第一批请求打到 Redis。Redis 6 的客户端缓存可以在键变更时收到失效通知，go-redis 能打开 tracking。

三类失效：

- 击穿：热点 key 过期，大量请求同时打到数据库。用 `singleflight` 在本进程里只放一个调用去加载，其他调用等这一个结果。多个进程仍靠 Redis 锁。`singleflight` 共享的是第一次调用，第一次的 `context` 取消会把等待者一起带成失败，加载用的超时要按这批调用里最宽的那个来。
- 穿透：查不存在的 key。固定的空值可以缓存，TTL 更短。每次都换 key 时，用布隆过滤器先挡。Redis 8 起开源发行版内置 `BF.ADD` / `BF.EXISTS`，更早用 RedisBloom 模块。位数组在结构那一节。过滤器说不存在就一定不存在；说存在仍可能是误判，那时再查库。元素只能加不能删，删数据要换一个新过滤器再灌一遍主键。
- 雪崩：一批 key 同时过期，或 Redis 本身不可用。TTL 加随机抖动。热点 key 用锁重建。Redis 不可用时要有限流和降级，而不是无限穿透到数据库。

强一致的数据留在数据库里。缓存这条链路做到最终一致：先写库，事务提交成功后再删 Redis。提交前删除会让并发的读把旧行又装回缓存。GORM 的事务回调放在函数返回 `nil`、事务已经提交之后；函数里中途删缓存，回滚了缓存也已经被删掉。

```go
err := db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
    if err := tx.Model(&Detail{}).Where("id = ?", id).Updates(detail).Error; err != nil {
        return err
    }
    return nil
})
if err != nil {
    return err
}
_ = rdb.Del(context.Background(), "detail:v3:"+id).Err()
```

删失败就记一条待删记录，后台再删，TTL 是最后一道。直接改缓存里的对象要处理局部更新和并发写，容易和数据库岔开。

有一个固定的竞态：请求 A 读到旧值，请求 B 写库并删掉缓存，A 再把旧值写回去。处理是写路径只删不改，读路径回填时带短 TTL；写完隔一小段再删一次，间隔要盖住正在回填的读。更稳的是订阅 binlog，提交已经发生之后再删缓存。Go 侧用 `go-mysql` 扮成从库，或者由独立进程把 binlog 推进 Kafka，应用只做消费者。

空值也缓存。布隆过滤器放在缓存之前，元素是已经存在的主键。误判率大约是 `(1 - e^{-kn/m})^k`：`m` 是位数，`k` 是哈希次数，`n` 是预计元素个数。超过预计容量，误判率会很快变高。

缓存键带上业务版本，例如 `detail:v3:{id}`。结构变了就换前缀，旧键等 TTL 自己消失。展示用的总库存可以短暂不准，放 Redis 并加随机 TTL。

多副本各自回源时，同一个冷 key 会把数据库打 N 次。groupcache 的做法是两层合在一起：一致性哈希决定这个 key 归哪一台，归它的那台用 `singleflight` 只回源一次，别的副本向这台要。它按容量淘汰，不靠 TTL，适合算完就不变的派生数据。商品详情这种会改的对象仍用带版本的短 TTL。热点 key 还是会打在负责它的那一台上，秒杀的写不走这条路径。

---

## 3. 计算机网络

应用看到的是一条 HTTP 请求。它实际穿过网卡、IP 路由、TCP 连接、TLS、HTTP，再进入进程。Go 服务把 TLS 放在 Ingress 或 SLB，`net/http` 对内用 HTTP/1.1。超时、连接池和幂等要按这几层分开设。

### 分层和一次请求

链路层把帧送到同一二层网络里的下一跳。IP 层按路由表选下一跳，不保证到达。TCP 在 IP 之上提供字节流：可靠、有序、有拥塞控制。UDP 只提供报文。HTTP 是请求响应的应用协议，跑在 TCP 上；HTTP/3 跑在 QUIC 上，QUIC 跑在 UDP 上。

一次跨机器调用的时间大致是这些量级：同机房内存访问约 100 纳秒，同可用区网络往返约 0.5 毫秒，跨可用区约 1 毫秒，固态盘读约 100 微秒。应用超时如果设成和同机房 RTT 一样短，正常抖动就会失败；如果设成几十秒，故障时 goroutine 和连接会被占满。

DNS 把名字解析成地址。纯 Go 的解析器（`CGO_ENABLED=0` 时就是它）按自己的方式读 `resolv.conf`，不走 cgo 那条 glibc 解析。Kubernetes 里 Pod 的 `resolv.conf` 常见 `ndots:5`：名字里的点少于 5 个时，先拼上搜索域再问绝对名字。`mysql.xxx.com` 这种外部名字也会先走一遍搜索域，多几次 NXDOMAIN，延迟就加在每次新建连接上。集群内服务用服务名，集群外地址用完整域名。连接池把「解析加握手」摊到很多请求上。

`net.Resolver` 的同步解析会堵住当前 goroutine。堵在网络上时，运行时的网络轮询器会把这个 goroutine 停住，线程可以去跑别的 goroutine。不要在持有 runtime 锁的地方做解析。自己写的解析要带 `context`。

### TCP

三次握手：客户端发 SYN，服务端回 SYN+ACK 并进入 SYN_RCVD，客户端再发 ACK，两边进入 ESTABLISHED。服务端在握手完成前把连接放在 SYN 队列，完成之后放进 accept 队列，等 `accept` 取走。SYN 队列满了，新的握手会被丢掉或触发 SYN Cookie。accept 队列满了，已完成的连接会被丢掉，客户端看到超时。队列长度受 `somaxconn` 和监听 backlog 里较小的那个限制。`net.Listen` 的默认 backlog 跟这个上限走。`ss -lnt` 里，监听套接字的 Recv-Q 是还没被 accept 的连接数，Send-Q 是 backlog。

`net/http` 在 accept 循环里每接受一条连接就启动一个 goroutine。这个 goroutine 负责读请求、调用 `Handler`、写响应。HTTP/1.1 上，一条连接同时只处理一个请求，下一个请求要等这一次响应写完。并发来自连接数和这些 goroutine，没有 Tomcat 那种固定的工作线程上限。上限在后面：`database/sql` 的池、下游的连接数、自己加的信号量。accept 队列堆高，说明这个进程接受连接的速度跟不上，先看是不是卡在启动，或者机器已经在节流。

四次挥手：主动关闭的一方发 FIN，对方回 ACK，之后对方发 FIN，主动方再回 ACK。主动关闭的一方进入 TIME_WAIT，等待 2MSL，用来让最后的 ACK 有机会重传，并让老的报文从网络里消失。TIME_WAIT 在主动关闭的那一端。HTTP 保活超时由服务端发起关闭时，TIME_WAIT 堆在服务端。它占的是四元组。`tcp_tw_recycle` 已经从内核里去掉。`tcp_tw_reuse` 只影响本机作为客户端向外发起的连接。

保活用来发现对方已经消失、但本机还以为连接在的情况。中间设备也会按空闲时间掐连接。应用的 `IdleTimeout`、负载均衡的空闲超时、数据库的 `wait_timeout`、`ConnMaxLifetime` 要放在同一套数上：越靠近调用方，空闲断开越晚，池里的连接才不会被对端先关掉。

Nagle 把小包攒一攒再发，延迟 ACK 让接收方等一等再确认。两个同时开着，小请求会多等一个延迟 ACK 的定时器。`net/http` 的传输层默认打开 `TCP_NODELAY`。

发送方实际能发出去的数据量取拥塞窗口和接收窗口里较小的那个。

流量控制保护接收方。接收方在 ACK 里通告 `rwnd`。缓冲满了就通告 0，发送方停住，之后用窗口探测问窗口有没有恢复。应用读得慢、响应体堆在写缓冲里，对端就会因为 `rwnd` 变成 0 而停发。

拥塞控制保护网络。慢启动把 `cwnd` 按指数放大，超过阈值后进入拥塞避免。丢包时 CUBIC 把窗口降下来再爬。BBR 按估算的带宽和 RTT 发，少依赖丢包当信号。`cwnd` 变小或丢包重传，应用看到的是长尾。同一可用区里的超时要能盖住偶发重传，跨地域调用要把超时和重试次数一起降下来。

半开连接是一端已经重启，另一端还拿着旧连接去写。写才会发现 RST，只读的连接要靠超时或 keepalive。所以读超时是必须的，不能只设拨号超时。

TCP 是字节流。一次 `Read` 可能拿到半条消息，也可能拿到两条消息粘在一起。HTTP 用 `Content-Length` 或分块编码做帧边界，`net/http` 已经把帧拆好再交给 `Handler`。自己写的协议要有长度字段或分隔符，先攒够一帧再交给业务。按一次 `Read` 当一条消息，就会在压力一大时解析错位。`bufio.Scanner` 默认的单行上限是 64KB，超过会直接报错，长行要换缓冲或者改用长度前缀。

`context` 取消不会自动打断一次已经堵在 `net.Conn.Read` 上的调用。`net/http` 和数据库驱动在取消时会把截止时间拨到过去，`SetReadDeadline` 对正在进行的读也生效，读才会带着 `os.ErrDeadlineExceeded` 返回。自己握着的连接要做同样的事：`context.AfterFunc` 里把读截止时间设成 `time.Now()`，读返回之后如果取消已经发生，再把截止时间清掉，避免这条连接上之后的读都立刻超时。只 `select` `ctx.Done()`、不拨截止时间，goroutine 会一直停在内核里。

### HTTP

HTTP/1.1 在一条 TCP 上串行请求和响应，靠 `Connection: keep-alive` 复用连接。管道化把多个请求一起写出，但服务器必须按顺序响应，实践里很少开。队头阻塞发生在这一层，也发生在 TCP 丢包时：一个包丢失，这条连接上后面的字节都要等重传。

HTTP/2 在一条 TCP 上多路复用多个流，头部用 HPACK 压缩。TCP 丢包时，这条连接上所有流仍然一起停。`http.Server` 在开启 TLS 时会协商 HTTP/2，一条连接上的并发流默认大约 250。前面有 Ingress 终结 TLS 时，进程看到的仍是 HTTP/1.1。HTTP/3 把流放到 QUIC 上，一个流丢包只挡住这个流。QUIC 的 0-RTT 能在恢复连接时提前发数据，这段数据可以被重放，非幂等的 POST 不能放进 0-RTT。

方法按幂等来记。GET、HEAD、PUT、DELETE 在协议语义上是幂等的。POST 不是。实际接口里 PUT 和 DELETE 仍然可能有副作用，能不能重试看服务有没有按幂等实现。状态码按谁的责任分：

| 状态码 | 含义 |
| --- | --- |
| 200 / 201 / 202 / 204 | 成功、已创建、已接受但还没做完、成功且没有正文 |
| 400 | 请求不合法。绑定和校验失败走这一档 |
| 401 / 403 | 没有身份、有身份但没有权限 |
| 404 | 资源不存在 |
| 409 | 和当前状态冲突，例如唯一键、幂等键对应了另一份请求体 |
| 429 | 被限流 |
| 500 | 服务自己没处理好 |
| 502 / 503 / 504 | 网关连不上上游、上游不可用、网关等上游超时 |

超时从外往里缩短。客户端 3 秒，网关 2.5 秒，服务调下游 2 秒。外层更长，内层的错误才能返回。网关先断开时，服务可能还在写数据库，调用方重试就会重复下单。所以写请求带幂等键，超时之后先查结果。

`http.Server` 的超时默认全是 0，也就是不限制。慢客户端可以一直占着连接和 goroutine。四个字段分开设：

| 字段 | 管什么 |
| --- | --- |
| `ReadHeaderTimeout` | 读完请求头的上限，用来挡慢速头 |
| `ReadTimeout` | 读完整个请求（含正文）的上限 |
| `WriteTimeout` | 从读完请求头开始，到响应写完。处理函数跑得久、还没开始写，也会被它切断 |
| `IdleTimeout` | 保活连接上，两个请求之间最多空多久 |

流式响应不要和普通接口共用一个很短的 `WriteTimeout`。`http.ResponseController` 可以按这条连接延长写截止时间。做不到时，流式接口单独听一个端口，那个端口的写超时放开，由应用层心跳决定连接还活着没有。

```go
srv := &http.Server{
    Addr:              ":8080",
    Handler:           router,
    ReadHeaderTimeout: 2 * time.Second,
    ReadTimeout:       10 * time.Second,
    WriteTimeout:      15 * time.Second,
    IdleTimeout:       60 * time.Second,
}
```

客户端按进程持有一份 `*http.Client`。`http.DefaultClient` 的 `Timeout` 是 0，会无限等。默认 `Transport` 的 `MaxIdleConnsPerHost` 是 2，一个热点下游上大部分连接被用完就关掉，下一跳又握手。`MaxConnsPerHost` 默认不限制。

```go
client := &http.Client{
    Timeout: 2 * time.Second,
    Transport: &http.Transport{
        DialContext: (&net.Dialer{Timeout: 200 * time.Millisecond, KeepAlive: 30 * time.Second}).DialContext,
        MaxIdleConns:          100,
        MaxIdleConnsPerHost:   20,
        MaxConnsPerHost:       50,
        IdleConnTimeout:       90 * time.Second,
        TLSHandshakeTimeout:   2 * time.Second,
        ResponseHeaderTimeout: 2 * time.Second,
        ExpectContinueTimeout: time.Second,
    },
}
```

`Client.Timeout` 覆盖拨号、重定向和读完正文的全部时间。一次调用要更短的预算时，用请求的 `context`。`Timeout` 和 `context` 以先到的为准。读正文用 `io.LimitReader` 或 `http.MaxBytesReader`，对端不关连接时不能一直读下去。`resp.Body` 必须 `Close`，否则连接回不到空闲池。

重试只针对幂等的 GET，以及带了幂等键的 POST。没有幂等键的下单，超时后先查结果，再决定要不要重发。重试要有次数上限和抖动，否则一个慢下游会让所有调用方同步放大流量。

入口在 TLS 终止后把 `X-Forwarded-For` 和 `X-Forwarded-Proto` 传进来。这两个头只信任直接相连的代理写进来的值。应用看到的对端地址是网关的地址，真实客户端地址在代理追加的头里。应用如果直接听公网，就不能用客户端自己带的转发头做访问控制。

### TLS

TLS 1.3 握手是 1-RTT：客户端发支持的参数和密钥份额，服务端回证书和自己的密钥份额，两边算出会话密钥，应用数据可以接着发。1.2 通常要 2-RTT。恢复会话时 1.3 可以 0-RTT，重放风险和 HTTP/3 的 0-RTT 一样。证书在入口上，进程里不再做握手。轮换证书是入口的配置，不用重新发布应用镜像。服务之间如果要 mTLS，由网格或网关终结，应用仍按 HTTP 处理身份头。

### 负载均衡

四层负载均衡按 IP 和端口转发，不看 HTTP。云上的网络型负载均衡、Kubernetes 的 Service 和 kube-proxy 都是这一层。一条连接被转到某个后端之后就留在那里，直到连接结束。

七层负载均衡看 Host、路径和头。Ingress、Nginx、API 网关是这一层。它可以按请求做路由、限流和 TLS。HTTP 长连接上的多个请求可以到不同后端，前提是代理在请求边界上转发。WebSocket 和 gRPC 流是一条长连接，会被钉在选中的那个后端上。

均衡算法：轮询适合实例能力相近的无状态服务。最少连接适合请求耗时差很多的场景。一致性哈希把节点和键映到同一个环上，键顺时针遇到的第一台负责它。一台离开，只有它和前一台之间那段弧上的键改道。每台物理节点在环上放很多虚拟节点，弧被切碎，数据不会整段堆到下一台。权重用来把新版本慢慢放进来。

健康检查失败才摘除。检查要打应用的就绪接口，只探 TCP 端口会把还没加载完的进程放进轮询。检查间隔、失败阈值和成功阈值决定摘除有多快、恢复有多稳。阈值太低，一次抖动就把实例摘掉，流量堆到剩下的实例上。

### gRPC

gRPC 跑在 HTTP/2 上。接口和字段写在 protobuf 里，`protoc` 生成 Go 的请求结构和 stub。一条 RPC 是 HTTP/2 上的一个流，消息按长度前缀放进 DATA 帧。字段号是合同的一部分：加字段用新的号，旧客户端忽略不认识的号；已经发出去的号不改含义、不复用。

四种调用。一元是一个请求一个响应。服务端流是一个请求之后持续回多条。客户端流是连续上传再收一个汇总。双向流是两边随时写。对外的 HTTP API 仍用 JSON。内部、字段稳定、或者要流式的调用用 gRPC。

`grpc.NewClient` 按进程建一条通道，stub 从通道生成。它默认不等待连接完成，第一次 RPC 才真正握手。截止时间放在 `context` 里，写进 `grpc-timeout`，沿调用链往下传。每一跳用剩余时间，剩余时间不够就返回 `DEADLINE_EXCEEDED`，不再往下调用。调用方没设截止时间时，服务端给一个自己的上限。

```go
conn, err := grpc.NewClient(
    "dns:///order.prod.svc:50051",
    grpc.WithTransportCredentials(insecure.NewCredentials()),
    grpc.WithDefaultServiceConfig(`{"loadBalancingConfig":[{"round_robin":{}}]}`),
)
ctx, cancel := context.WithTimeout(ctx, time.Second)
defer cancel()
resp, err := stub.CreateOrder(ctx, req)
```

一元 RPC 看起来像一次函数调用，实际占着当前 goroutine 直到返回。这个 goroutine 堵在网络上时，线程可以去跑别的 goroutine。不要在 gRPC 的回调里再去做会阻塞很久的本地计算。

状态码和 HTTP 的不是同一套。`OK` 成功。`CANCELLED` 是调用方取消。`DEADLINE_EXCEEDED` 是截止时间到了。`NOT_FOUND`、`ALREADY_EXISTS`、`FAILED_PRECONDITION` 是业务前置条件。`RESOURCE_EXHAUSTED` 是配额或限流。`UNAVAILABLE` 是连接不上或服务端暂时不能接，可以对幂等的方法重试。`INTERNAL` 是服务端没处理好。用 `status.FromError` 取出码，不要用字符串比较。

元数据用来传认证和 `traceparent`，不把它们塞进业务消息。默认单条消息大约 4MB，大文件走对象存储，RPC 里只传地址。拦截器按进程挂在通道上，做认证、日志和传播追踪。业务错误在返回值里用 status 表达，拦截器不吞掉它。

负载均衡要按 RPC 而不是按连接。一条 HTTP/2 连接上可以跑很多 RPC。四层负载均衡选定后端之后，这条连接上的全部 RPC 都进同一个 Pod。副本扩了也用不上。两种接法：无头 Service 把 Pod 地址交给客户端，客户端用 `round_robin`；或者用能识别 HTTP/2 流的代理，按 RPC 转发。默认的 `pick_first` 只会盯住解析结果里的第一台。

空闲连接会被中间设备掐掉。keepalive 要比这条空闲时间更勤，同时不要短于服务端允许的最小 ping 间隔，过勤会被 `GOAWAY`。通道在进程退出时 `Close`。

### WebSocket 与 SSE

这两条都是服务器持续往浏览器推数据。差别在方向和连接怎么活。

WebSocket 从一条 HTTP 请求升级，成功是 101，之后双方都可以发帧：文本、二进制、ping、pong、关闭。它适合聊天、协同编辑这种两边都要主动写的场景。帧上没有 HTTP 头，认证放在升级请求的 Cookie 或 `Authorization` 里。令牌放在查询串里会进访问日志。升级用 `http.Hijacker` 把连接从 `net/http` 的读写循环里拿出来。`Server.Shutdown` 不会等待已经被劫持的连接，这些连接要自己登记，在停机时写关闭帧再关掉。

SSE 仍是一条 HTTP 响应，`Content-Type` 是 `text/event-stream`。服务器往下写事件，浏览器不能在这条响应上反向写业务数据。客户端要发命令就另开 POST。它适合通知、任务进度、模型逐字输出。

一条事件以空行结束。`id` 是续传位置，`event` 是类型，`data` 是正文。

```go
func events(w http.ResponseWriter, r *http.Request) {
    flusher, ok := w.(http.Flusher)
    if !ok {
        http.Error(w, "stream unsupported", http.StatusInternalServerError)
        return
    }
    w.Header().Set("Content-Type", "text/event-stream")
    w.Header().Set("Cache-Control", "no-cache")
    w.Header().Set("X-Accel-Buffering", "no")
    lastID := r.Header.Get("Last-Event-ID")
    for {
        item, err := nextItem(r.Context(), lastID)
        if err != nil {
            return
        }
        lastID = item.ID
        fmt.Fprintf(w, "id: %s\ndata: %s\n\n", item.ID, item.Body)
        flusher.Flush()
    }
}
```

`Flusher` 把缓冲刷到客户端。中间没有这下调用，数据会堆到缓冲满或者处理函数返回。`r.Context().Done()` 在客户端断开时关闭，循环要看它，不然 goroutine 会一直写一个已经没人读的响应。`WriteTimeout` 会把这条长响应切断，流式端口按上一节把写超时放开，心跳间隔短于网关的空闲断开。

浏览器的 `EventSource` 只会发 GET，会带 Cookie，不会带 `Authorization`。断线后它自动重连，并带上 `Last-Event-ID`。Nginx 默认会缓冲响应，`X-Accel-Buffering: no` 加上代理侧关掉缓冲，事件才能立刻到客户端。

一条 SSE 在 HTTP/1.1 上占住浏览器到这个域名的一个连接名额。HTTP/2 上它只是一个流。WebSocket 升级之后不再受这个名额限制，但网关要允许升级。

多副本时，连接落在哪一台，消息就要能送到那一台。每台进程把自己接住的会话放在内存里。要广播时，先发到 Redis，各进程再写自己的连接。Pub/Sub 不留消息，断线期间的内容要靠 Redis Stream 或数据库按 id 补。

心跳间隔短于负载均衡和网关的空闲断开时间。WebSocket 用 ping，SSE 用注释行或一条心跳事件。滚动更新会把这条长连接断开，客户端重连到新后端，而不是每个请求平稳地切过去。

---

## 4. 操作系统（Linux）与 Go 运行时

Go 进程跑在这套机制上：虚拟内存、页缓存、调度、epoll、信号、cgroup。容器是同一套机制划出来的视图。运行时在上面再加一层 goroutine、网络轮询和垃圾回收。用户态看到的并发单位是 goroutine，操作系统看到的仍是少量线程。

### 进程、线程与 goroutine

进程有独立的地址空间、文件描述符表和 PID。线程共享地址空间，各自有栈和寄存器。goroutine 是运行时调度的协程，起始栈大约 2KB，不够时按倍拷贝到一块更大的连续栈，上限默认 1GB，可以用 `debug.SetMaxStack` 改。运行时还会按历史增长情况，把新 goroutine 的起点抬高，避免每个请求都马上扩一次栈。创建成本从高到低是进程、操作系统线程、goroutine。隔离性反过来。

调度用三个东西：

| 名字 | 是什么 |
| --- | --- |
| G | goroutine。栈、寄存器现场、状态 |
| M | 操作系统线程。真正在 CPU 上跑 |
| P | 逻辑处理器，个数是 `GOMAXPROCS`。本地运行队列挂在它上面 |

能同时跑用户代码的 G 不超过 P 的个数。一个 G 堵在通道、锁或网络上时，不占着 M。堵在系统调用上时，这根 M 会跟进内核，P 可以解绑到另一根 M 上，继续跑别的 G。cgo 调用占着一根 M，和阻塞系统调用是同一类成本。`GOMAXTHREADS` 默认大约一万，线程数顶到这里进程会崩。

G 的状态就这几档：可运行、正在某个 M 上跑、在系统调用里、在等待（通道、锁、网络、定时器、睡眠）、已退出。等待中的 G 不占用 P。操作系统里的 R、S、D、Z 是线程和进程的状态。load average 是可运行和不可中断睡眠的进程数的滑动平均，不等于 CPU 使用率。一个 Go 进程可能只有几根线程，里面有几十万个在等网络的 goroutine，load 可以不高。

本地运行队列空了，P 会从别的 P 偷一半过来，也会去全局队列拿。大约每 61 次调度会看一眼全局队列，避免只进全局队列的 G 饿死。`sysmon` 不占 P，负责把在系统调用里待太久的 P 抢回来，也负责给跑了太久的 G 发抢占信号。

Go 1.14 起，抢占是异步的。紧循环里没有函数调用，运行时仍可以用信号把 G 从 M 上拿下来。抢占点之外仍有一小段不能被抢走：运行时正持有自己的锁、栈正在拷贝、写屏障的关键段落。`runtime.Gosched` 只是主动让出一次，不能拿它当公平调度。

网络调用走到运行时的 netpoller，Linux 上是 epoll。连接还没可读，G 被挂起，M 去跑别的 G。所以一个进程可以扛很多同时在等的连接，线程数接近 `GOMAXPROCS` 再加少数阻塞在系统调用里的 M。这和「一个请求一条操作系统线程」不是同一条成本曲线。真正的上限是连接池、文件描述符和下游，不是线程栈。

CPU 密集的计算仍会占满 P。请求路径上的压缩、加密、大循环会把 P 占住，别的 goroutine 要等抢占。这种工作放进单独的、带上限的 worker，或者丢进队列。并行度按 P 的个数，再多只是在运行队列里排队。

1.22 起，`for` 的循环变量每次迭代一份，闭包里捕获的是这一次的值。更早的版本要先把变量拷到循环体内再启动 goroutine。基线是 1.27，按每次迭代一份来写。循环里启动的 goroutine 仍要有退出条件，请求取消时要能停下来。

`go` 出去的函数如果 panic 没人接，整个进程退出。`net/http` 只接住自己调用的 `Handler` 里的 panic，打出栈并拆掉连接，进程还在，响应也不一定是整齐的 500。Gin 的 Recovery 在还没写出响应时补 500。自己启动的 goroutine 要在入口接住 panic，或者保证里面不会 panic。

### 内存模型与同步

Go 的内存模型用 happens-before 规定什么时候一个 goroutine 的写入对另一个可见。没有这层关系的普通读写，另一个 goroutine 可以一直看见旧值，编译器和 CPU 也可以重排。

这些关系可以直接用：

- 通道上的发送 happens-before 对应的接收完成。
- 关闭通道 happens-before 收到零值的那次接收。
- `Mutex` 的 `Unlock` happens-before 下一次成功的 `Lock` 返回。
- `Once.Do` 里函数的返回 happens-before 任何一次 `Do` 返回。
- `sync/atomic` 的操作带有明确的顺序。普通的 `bool` 标志没有。

`i++` 这种复合动作仍然会丢更新。计数用 `atomic.Int64`，或者在更高竞争下把计数拆开再汇总。原子变量适合标志和计数，不适合要读改写一组字段的临界区。

`-race` 按 happens-before 检查并发访问。它会多占内存和 CPU，放在测试和预发，不放进生产二进制。报了竞争就说明两个 goroutine 碰了同一块内存且没有同步，先补同步，再谈要不要用通道重写。

`sync.Mutex` 不可重入。同一 goroutine 再 `Lock` 一次，会把自己堵死，也没有别的 goroutine 能进来解。没有竞争时用原子操作在用户态拿到锁。竞争激烈时先短暂自旋，再进入运行时的等待队列。等锁超过大约一毫秒会进入饥饿模式，把锁直接交给队头，避免新来的 goroutine 一直插队。

`RWMutex` 在已经有写者等待时，新的读者排在写者后面，避免写者饿死。它同样不可重入。持有读锁时再去拿这把锁的写锁，会和自己死锁。读多写少、临界区又比一次原子操作长时，读写锁才划算。

| 要做的事 | 用法 |
| --- | --- |
| 互斥 | `sync.Mutex` |
| 读多写少 | `sync.RWMutex` |
| 等一组任务做完 | `sync.WaitGroup`，或者 `errgroup` |
| 只做一次初始化 | `sync.Once`。`Do` 里的函数 panic 了，Once 仍算做完 |
| 同时只放 n 个进入 | `golang.org/x/sync/semaphore` |
| 有界并发任务 | `errgroup` 的 `SetLimit` |
| 同一 key 只算一次 | `singleflight` |
| 短命的临时对象 | `sync.Pool`。GC 会清掉，不能当连接池 |
| 并发 map，键基本不重叠或读远多于写 | `sync.Map`。一般的缓存用普通 map 加锁 |

`sync.Map` 内部分成一把很少上锁的 read 和一把需要锁的 dirty。读多、键稳定时，读走原子路径。键一直在增删时，它会反复提升 dirty，比一把 `Mutex` 加普通 map 更贵。

`sync.Pool` 按 P 分本地池，GC 时把当前池挪成 victim，再下一次 GC 清掉。拿出来的对象要重置状态再放回去。它用来复用临时缓冲，连接、请求上下文、带业务数据的结构不要放进去。

通道用来传递所有权：送出去之后，发送方不再碰这块数据。

- 无缓冲通道是一次会合，发送方和接收方都到了才继续。
- 有缓冲通道在缓冲没满时，发送方不用等接收方。
- 向已经关闭的通道发送会 panic，关闭已经关闭的通道会 panic，关闭 nil 通道会 panic。
- 从已经关闭的通道接收，会先把缓冲里剩下的值取完，然后一直得到零值和 `ok == false`。
- nil 通道上的发送和接收永远堵着。放进 `select` 时，这个分支永远不会被选中，用来临时关掉一条分支。
- `select` 在多个已经就绪的分支里伪随机挑一个。有 `default` 时，没有分支就绪就走 `default`。

`select` 不保证公平到「谁等得久选谁」。要优先级就分成两次 `select`，第一次只放高优先级的分支和 `default`。

通道在运行时是一把锁加一个环形缓冲：发送下标、接收下标、发送等待队列、接收等待队列。等在队列里的 goroutine 包成 sudog。无缓冲通道的缓冲长度是 0，发送方和接收方在这把锁里配对后一起被唤醒。`select` 要同时等多个通道时，按通道地址的顺序加锁，避免两个 goroutine 各锁一个然后死锁。就绪分支先打乱再挑，所以没有固定优先级。

`context` 的取消是一次性的。父取消，从它派生的子都取消。`WithValue` 的键用未导出的类型，避免和别的包撞键。值只放请求范围内的数据：请求 id、已经验过的用户。用它传可选参数，调用链会变成一串隐式输入。`context` 不放进结构体里长期保存，随函数参数往下传。`http.Request` 自己带着 context，是这条规则的入口，不是反例。

`errgroup` 把一组 goroutine 收成一次 `Wait`。`WithContext` 在第一个错误出现时取消共享的 context，其他 goroutine 要看这个 context 才能停下来。不看的那个会继续占着连接，直到自己的调用超时。

### 内存与 GC

每个进程有自己的虚拟地址空间。页表把虚拟页映射到物理页。缺页时内核分配物理页或从磁盘把文件页读进来。`free` 里的 available 才是还能给应用用的内存，buff/cache 里的页缓存可以回收。

页缓存是文件读写的中间层。`write` 返回只说明进了页缓存，`fsync` 才把脏页交给磁盘。数据库的提交要 fsync 日志。容器的可写层不适合放要保留的数据。

脏页由 `dirty_ratio` 和相关阈值控制。写得太猛时，进程会在 `balance_dirty_pages` 里停住。这和 InnoDB 刷脏页是同一类现象，一层在内核，一层在缓冲池。

Go 的堆由逃逸分析决定谁上去。编译器能证明一个变量在函数返回后不再被引用，它就留在栈上，随 goroutine 一起回收。返回指针、放进接口、被闭包捕获、送到通道、放进切片后切片本身逃逸，这个变量就会到堆上。`go build -gcflags=-m` 能看到逃逸结论。热路径上少把短命对象送进接口和通道，分配速率会下来，GC 的压力跟着下来。

堆上的小对象不直接向操作系统要。分配器按页管理，一页 8KB。不超过 32KB 的对象向上取整到某个 size class，大约七十档。每个 size class 再分成「含指针」和「不含指针」两种 span，GC 只扫描含指针的那一种。当前 P 的 mcache 里这个 span 还有空位，分配不加锁。没有空位就向 mcentral 要一整段 span。mcentral 也没有，就向 mheap 要页。mheap 没有足够的页，再向操作系统要至少大约 1MB，把要一次页的成本摊开。超过 32KB 的对象跳过 mcache 和 mcentral，直接在 mheap 上找连续的页。

不含指针、并且小于 16 字节的对象走 tiny allocator：几个小对象拼进同一个 16 字节的块，整块都不可达才回收。它主要收短字符串和逃逸出去的小变量。这种对象不能单独 `free`，所以显式释放的路径不会走这里。

`string` 和 `[]byte` 互相转换，语言语义是拷贝。编译器只在能证明结果不逃逸、并且之后没有被改时才省掉这一次。热路径上能留在一种表示里就不要来回转。结构体字段按对齐从大到小排，否则字段之间会插入填充，切片里每个元素都多占这份填充。先用 allocs profile 确认分配在这里，再改布局。

切片是一块底层数组加上长度和容量。`append` 在容量够时就地写，不够时分配一块新数组再拷贝。容量小的时候大约翻倍，大了以后增长比例降到大约四分之一，再向上对齐到分配器的尺寸等级。循环之前能知道长度，就 `make` 出来再按下标填。两个切片共享底层数组时，一个的 `append` 可能覆盖另一个还在看的元素。

map 从 1.24 起用 Swiss Table。一张 map 拆成多张表，每张表由 8 个槽位的组组成，组头用一段控制字节记录空、删除和哈希的低位，查找时可以一次比一组。单张表大约装到 1024 个元素就拆开，一次插入的扩容上限是搬走这一张表，不会把整张 map 一次拷完。1.24 之前是 8 个槽位的桶加溢出链，写入时渐进搬迁。两种实现都不允许并发读写。并发的写，或者一边写一边读，运行时直接 `fatal error: concurrent map writes`，`recover` 接不住，进程退出。要并发就加锁，或者用 `sync.Map`。遍历 map 的顺序每次都不同。遍历时可以删除当前键，插入的新键可能被访问到，也可能不会。

接口有两种。空接口是类型指针加数据指针。带方法的接口是 itab 加数据指针。itab 里有类型和方法表。一个值为 nil 的指针放进接口之后，接口本身不是 nil，因为类型信息还在。错误返回最容易踩中：函数声明返回 `error`，里面 `return (*MyError)(nil)`，调用方的 `err != nil` 为真。返回错误时，没有错误就 `return nil`，不要返回带类型的空指针。

方法集决定一个类型能不能赋给接口。值的方法集只有值接收者。指针的方法集包含值接收者和指针接收者。接口方法是指针接收者时，赋进去的必须是指针；赋一个值，编译期就失败。可寻址的值在直接调用时，编译器会自动取地址，这一步不会发生在赋给接口的时候。大结构体用值接收者，每次调用都拷贝一份。存在接口里之后，具体类型要到断言或类型开关才拿得回来。

`defer` 按后进先出执行，参数在 `defer` 这一行就求值。`recover` 只在直接由 `defer` 调用的函数里生效，而且只接当前 goroutine 的 panic。1.14 起，次数确定、不在循环里的 `defer` 多数会改写成打开的代码，不再为每次调用单独分配。循环里的 `defer` 要等到函数返回才执行，循环很长时资源会一直占着，改为在循环体内用匿名函数包住，让 `defer` 在每次迭代结束时运行。

回收是并发的三色标记加清扫，不分代，也不把对象挪来挪去压紧。标记从根出发：全局变量、各 goroutine 的栈、寄存器。走不到的对象才是垃圾。

1. 标记开始时有一次很短的停顿，打开写屏障。
2. 标记和业务 goroutine 一起跑。堆上的指针写要过混合写屏障：既挡住「把已经扫描过的对象里的指针改掉」，也挡住「新写入的指针指向还没扫描的对象」。栈上的写不过写屏障，goroutine 停下来时整段栈被扫描。
3. 分配太快的 goroutine 要做标记辅助，分配速率被标记速率拉住。接口变慢、CPU 花在 GC 上，经常就是辅助在干活。
4. 标记结束再一次短停顿。
5. 清扫是并发的，和分配绑在一起：要新的 span 时顺手把旧页清干净。

`GOGC` 默认 100：堆比上一轮标记结束时的活对象大约再长一倍，就再触发一轮。`GOMEMLIMIT` 从 1.19 起是软上限，环境变量和 `debug.SetMemoryLimit` 都能设。离上限远时按 `GOGC` 的比例触发，靠近上限时 GC 更勤，把还没使用的堆收回来。它是软的：GC 有自己的 CPU 份额，不会为了卡死上限把所有 P 都拿去回收；活对象本身已经超过这个值时，堆也收不回去。容器里把它设在 memory limit 的八到九成，给 goroutine 栈、cgo 和运行时之外的占用留余量。只关 `GOGC`、只靠内存上限，流量一低内存会停在上限附近，下一次脉冲就没有余量。

1.26 起默认的收集器是 Green Tea。它改进的是小对象的标记和扫描，让这一步更照顾缓存局部性，官方给的范围是 GC 开销大约下降一到四成。构建时 `GOEXPERIMENT=nogreenteagc` 可以退回，1.26 的说明预计这个开关以后会去掉。业务侧要调的仍是分配速率、`GOGC` 和 `GOMEMLIMIT`。

空出来的堆由 scavenger 还给操作系统。Linux 上默认用 `MADV_DONTNEED`，RSS 会下来，但不是在 GC 结束的同一瞬间归零。按内存使用率做自动扩容时，阈值按稳定后的 RSS 设。用一块不使用的大切片去抬高 GC 目标，是 `GOMEMLIMIT` 出现之前的做法，现在不要再留这块配重。

cgroup 内存用尽由内核杀进程，退出码 137，Go 的 panic 来不及抛。Pod 状态里是 `OOMKilled`。`GOMEMLIMIT` 设得太接近 limit，GC 已经在拼命收，仍可能和内核的杀死赛跑。堆外的 cgo、直接 `mmap` 的缓存不在这个软上限的账上。

交换分区会把匿名页换出内存。数据库、Redis 和这台应用的机器关掉交换。容器节点同样关掉交换，否则内存限制和驱逐的判断会被交换搅乱。

切片、map、接口的分配走同一套 size class。小对象（几十到一百字节以内）在 1.27 里有按尺寸特化的分配路径，分配本身更便宜。这不改变逃逸和 GC 的关系：短命对象少逃逸，仍然比换一条分配路径更有效。

### 泛型

类型参数用来写「算法不关心元素具体类型」的函数和类型。行为会变的依赖仍然用接口：支付、仓储、下游客户端。把元素从 `interface{}` 改成类型参数，是为了去掉断言，并且让错误的类型在编译期失败。标准库里的 `slices`、`maps`、`cmp` 已经覆盖排序、查找和拷贝，业务代码先用这几个包。

约束是一个类型集合。`any` 是所有类型。`comparable` 是能用 `==` 的类型，做 map 的键时需要它。`int64 | string` 这种联合是集合的并。`~int` 表示底层类型是 `int`，于是 `type Money int` 能匹配 `~int`，匹配不了写成 `int` 的约束。排序用 `cmp.Ordered`。

```go
type List[E any] struct {
    elem E
    next *List[E]
}

func (l List[E]) Map[R any](f func(E) R) List[R] {
    // 1.27 起方法可以再声明自己的类型参数
    return List[R]{}
}
```

1.18 就能写泛型类型和泛型函数。方法当时不能再带类型参数，`Map` 只能做成包级函数，调用是从里往外套。1.27 起具体类型上的方法可以声明自己的类型参数，`Map` 留在类型旁边，调用从左往右。标准库的例子是 `math/rand/v2` 的 `(*Rand).N[Int intType](n Int) Int`。泛型方法在调用前必须实例化，实参写出来或者由参数推断。1.27 同时放宽了函数类型的推断：把泛型函数赋给一个已经写明参数类型的函数变量时，类型参数可以推断出来。

接口方法不能声明类型参数，编译器直接拒绝。泛型方法也不能用来实现一个接口方法，哪怕某一次实例化之后的签名刚好一样。要满足接口，就写一个没有类型参数、签名完全一致的方法。模拟和替换依赖仍然针对接口，不针对一个泛型方法。

实现上不是每种具体类型各生成一份机器码，也不是把类型擦掉只留一份。编译器按 GC shape 生成函数体：两个具体类型的底层类型相同，或者两者都是指针，就共用一份。所有指针类型共用一个 shape。`int` 和 `*int` 不共用，`int` 和 `int32` 也不共用。每次调用带着一本编译期生成的字典，里面是这个具体类型的大小、对齐，以及函数体要调用的方法。泛型函数内部的方法调用走这本字典，是一次间接调用。PGO 能把热的间接调用收成直接调用，前提是 profile 里这个类型真的热。热路径上永远只有一个具体类型时，直接写这个类型。类型参数留给排序、集合、对象池这种算法。HTTP 的边界仍然是结构体和 JSON，给每个处理函数加类型参数不会让协议变快。

约束写成 `any`、函数体内再做类型开关，类型参数没有带来信息，接口更直接。联合约束堆出一棵继承树，调用方要读完整个集合才知道能传什么。

### 磁盘、IO 与网络轮询

一次 IO 有五种历史模型。阻塞 IO：线程停在 read 上直到数据到。非阻塞 IO：没有数据就立刻返回。IO 复用：用 epoll 先等哪些 fd 就绪，再去读。信号驱动：就绪时发信号。异步 IO：内核完成后通知应用，Linux 上现在用的是 io_uring。

Go 的网络走 netpoller。goroutine 发起读，fd 还没就绪，G 被挂到 netpoller 上，M 继续跑别的 G。水平触发的语义由运行时消化，业务代码阻塞在 `Read` 上即可。磁盘上的普通 `Read` 仍可能让这根 M 进入不可中断睡眠，P 会被解绑。请求路径上对大文件做阻塞读写，会多占线程。大文件下载交给网关或对象存储，应用只签发地址。真正不挡住线程的磁盘 IO 用 io_uring 的库，或者放进单独的 worker。

epoll 在 fd 很多时用事件回调，不用像 select 那样每次拷贝整张 fd 集合。一个进程里的网络 fd 可以很多，线程数仍然接近 P 的个数。在请求里做一段不让出的大计算，效果和把 netpoller 停住类似：P 被占着，就绪的连接没有人读。

零拷贝里，`sendfile` 由内核在页缓存和套接字之间拷贝，数据不进用户缓冲区。Nginx 把静态文件发给客户端走的是这条。应用自己 `Read` 进切片再 `Write` 出去，是两次多余的拷贝。`mmap` 把文件映射进地址空间，适合按页随机读。

inode 是文件的元数据，目录项把名字指到 inode。删除打开着的文件只是拿掉目录项，进程还握着 fd 时空间不会释放。日志切割之后，进程如果继续写已经删除的 fd，磁盘还在涨，`ls` 却看不到文件。Go 服务把日志打到 stdout，由容器运行时收集。

文件描述符上限在 `/proc/<pid>/limits` 里。每个连接、每个打开的文件各占一个。`too many open files` 先看是连接泄漏还是上限太低。`http.Client` 的空闲连接、数据库池、Redis 池各算各的。`resp.Body` 不关、`Rows` 不关，是泄漏的常见来源。

### 信号和停机

`SIGTERM` 是请进程退出。`signal.NotifyContext` 接到之后开始关闭。Go 运行时默认会在 `SIGTERM` 上退出；一旦 `Notify` 把信号接走，就要自己走完关闭再 `return`，否则进程会一直留到宽限时间耗尽，被 `SIGKILL`。`SIGKILL` 不能捕获。`SIGSTOP` 也不能捕获。容器停止先发 `SIGTERM`，宽限时间到了再 `SIGKILL`。

二进制自己做 PID 1 时，Go 的信号处理仍然有效。shell 形式的 `ENTRYPOINT /app` 会让 shell 成为 PID 1，信号打在 shell 上，进程收不到，最后被强杀。用 exec 形式。业务进程不拉子进程时，不需要再套一层 init。要拉子进程，由 PID 1 负责 `Wait`，否则会留下僵尸。

关闭顺序：先 `Shutdown` 拒绝新连接、等已经进来的请求，再关闭数据库、Redis、消息客户端，最后等后台 goroutine。`Shutdown` 不等待被 `Hijack` 走的连接。宽限时间要盖住这段等待。`http.ErrServerClosed` 是正常退出，不要当成启动失败。

Kubernetes 的滚动更新用新 Pod 替换旧 Pod，监听端口跟着 Pod 走。虚拟机上要原地换二进制时，新进程用 `SO_REUSEPORT`，或者继承旧进程交出来的 listener，开始 accept；旧进程把已经接住的请求处理完再退出。两个进程会同时活着，数据库连接数按两个进程算。容器里再做一次端口继承，是在和 kubelet 的替换抢同一个端口。

### 看一台机器

| 现象 | 看什么 |
| --- | --- |
| CPU 打满还是在等 | `vmstat 1` 的 us、sy、id、wa、st。st 是被宿主机抢走的时间 |
| 哪个线程在用 | `top -H -p <pid>` |
| goroutine 堵在哪 | `curl localhost:6060/debug/pprof/goroutine?debug=1` |
| 堆 | `go tool pprof` 的 heap，`GODEBUG=gctrace=1` |
| 泄漏的 goroutine | 1.27 的 `/debug/pprof/goroutineleak` |
| 磁盘是否饱和 | `iostat -x 1` 的 await 和 `%util` |
| 连接堆在哪 | `ss -s`，`ss -tan` 按状态数 |
| 是不是被 OOM | Pod 的 `OOMKilled`，或 `dmesg` |
| 文件描述符 | `/proc/<pid>/limits` 和 `/proc/<pid>/fd` |

pprof 的端口只绑在本机或内网的管理端口上，不和业务端口混在同一个公网入口。CPU profile 采样大约 30 秒，heap 看的是当前存活的对象，`allocs` 才是累计分配。锁和通道的阻塞 profile 要先把 `SetMutexProfileFraction`、`SetBlockProfileRate` 打开，否则采样是空的。

`st` 持续大于 0，是 CPU 限额或邻居把时间抢走了。`wa` 高是在等磁盘。goroutine 数一直涨、CPU 却不高，先看是不是下游超时太长，每个请求都堵着不返回。

1.25 起有执行轨迹的 flight recorder，在内存里留一段滑动窗口。出错时把这段窗口倒出来，比事后再去打一份 profile 更接近现场。导出的文件同样不要放到公网。

### cgroup

cgroup 限制和统计一组进程的 CPU、内存、IO 和进程数。cgroup v2 是一棵统一的树。Kubernetes 的 requests 用来调度，limits 写成 cgroup 上限。CPU limit 是 `cpu.max` 里的配额，用完这个周期的配额，进程被节流。被节流的时间在 `container_cpu_cfs_throttled_seconds_total` 里。内存 limit 是 `memory.max`，超了就 OOM。超内存是杀进程，超 CPU 是变慢。

1.25 之前，`GOMAXPROCS` 默认等于机器的逻辑 CPU 数，不看容器的 limit。GC 和标记辅助会按这个数起线程，配额很小的 Pod 会被节流一整段周期，尾延迟抬高。1.25 起，Linux 上默认取逻辑 CPU 数、CPU 亲和性掩码和 cgroup CPU limit 里较小的那个。limit 按 quota/period 算出吞吐上限，带小数时向上取整。只要机器上至少有 2 个逻辑 CPU，这个默认值就不会低于 2：limit 是 0.5 或 1，`GOMAXPROCS` 仍是 2。CPU request 不参与。limit 或核数变化时，运行时大约每秒看一次并更新。

这两件事在手动设过 `GOMAXPROCS` 之后都会停，包括环境变量和 `runtime.GOMAXPROCS` 的调用。`runtime.SetDefaultGOMAXPROCS` 能把自动行为装回来。`GODEBUG=containermaxprocs=0` 和 `updatemaxprocs=0` 可以分别关掉。1.25 之前用来在启动时按 limit 设一次的 `automaxprocs`，内部就会调用 `runtime.GOMAXPROCS`。升到 1.25 之后还留着它，周期更新就不再发生。没有特殊原因就不要再设。

`GOMAXPROCS` 限制的是同时跑的 G 的个数。CPU limit 限制的是一个周期里的 CPU 时间。两者接近时，节流会少很多。特别尖的突发会因为 P 的个数上不去而排队，这是用尾延迟换掉「整段被暂停」。

namespace 划出进程能看见的资源：PID、挂载点、网络栈、主机名、用户、IPC。容器里的 PID 1 在宿主机上只是一个普通进程。Service 的 ClusterIP 是集群网络里的地址，由 kube-proxy 或 eBPF 转成 Pod IP。

---

## 5. 开发框架

框架负责把 HTTP 变成一次函数调用，并管好参数和生命周期。业务规则留在服务自己的包里。这里以标准库 `net/http` 为服务器，Gin 为路由和中间件。小服务可以直接用 1.22 以后的 `http.ServeMux`。

### 服务器怎么把应用拉起来

依赖在 `main` 里按顺序构造：配置、日志、数据库、Redis、HTTP 客户端，最后是路由和 `http.Server`。缺一项就起不来。接口定义在使用它的包旁边，实现放在知道具体驱动的包里。`google/wire` 只是生成这段构造代码，不在请求路径上做反射装配。

```go
func main() {
    cfg := loadConfig()
    logger := newLogger()
    db := openDB(cfg.MySQL)
    rdb := openRedis(cfg.Redis)
    router := newRouter(logger, db, rdb)
    srv := &http.Server{
        Addr:              cfg.Addr,
        Handler:           router,
        ReadHeaderTimeout: 2 * time.Second,
        ReadTimeout:       10 * time.Second,
        WriteTimeout:      15 * time.Second,
        IdleTimeout:       60 * time.Second,
    }
    // 监听信号，Shutdown，然后按相反顺序关闭 rdb 和 db
}
```

配置在启动时读完并校验，缺字段就退出。环境变量适合非机密的开关。业务开关进配置中心，不靠重新编译。进程已经读进内存的配置不会因为环境变量改了就变，换地址要重建池，跟着滚动更稳妥。

Go 1.22 起，标准库的 `ServeMux` 能按方法和路径注册，路径里可以写 `{id}`，用 `r.PathValue` 取值。它没有中间件栈，包一层 `func(http.Handler) http.Handler` 就是中间件。Gin 在这之上提供分组、绑定和一份用起来固定的上下文。

Gin 的默认引擎带 Logger 和 Recovery。生产用 `gin.New()`，自己挂 Recovery 和访问日志，避免调试日志把正文打出来。`gin.SetMode(gin.ReleaseMode)` 关掉调试输出。默认信任所有代理，`ClientIP` 会采信客户端自己带的 `X-Forwarded-For`。启动时把可信代理设成入口网关的网段。

`*gin.Context` 在请求结束后会被放回池里复用。不能把它传给一个会活过这次处理函数的 goroutine。要带走的是 `c.Request.Context()`、路径参数的字符串拷贝，以及已经绑定好的结构体。`c.Copy` 也只适合还在这次请求里用的场景。

### 请求怎么走到函数

一条请求的路径是：`net/http` 读完 HTTP，Gin 按注册顺序穿过中间件。中间件里的 `c.Next()` 把请求交给后面的链，函数返回后才执行 `Next` 后面的代码，用来记耗时和状态码。`c.Abort` 不再调用后面的处理函数，已经写过的响应头还在。后注册的中间件在更外层还是更内层，以这条引擎实际的注册顺序为准，鉴权和请求 id 放在业务处理函数之前。

```go
func requestID() gin.HandlerFunc {
    return func(c *gin.Context) {
        id := c.GetHeader("X-Request-Id")
        if id == "" {
            id = newID()
        }
        ctx := context.WithValue(c.Request.Context(), reqIDKey{}, id)
        c.Request = c.Request.WithContext(ctx)
        c.Header("X-Request-Id", id)
        c.Next()
    }
}
```

绑定用 `ShouldBindJSON`。校验失败时由处理函数返回 400。`BindJSON` 会自己写 400 并 `Abort`，中间件容易把它和业务错误混在一起。字段约束写在结构体的 `binding` 标签上，处理函数里拿到的已经是形状合法的对象。校验只证明形状合法，订单归属和状态机留在服务里。

对外的 JSON 用结构体，不要用 `map[string]any` 再把内部字段一股脑编码出去。未知字段在 `encoding/json` 的 v1 行为里会被忽略。1.27 的 `encoding/json` 实现已经换到 v2 引擎上，v1 的 API 和行为还在；`encoding/json/v2` 默认拒绝非法 UTF-8 和重复字段名。对外合同要不要拒绝重复字段，在网关或绑定这一层显式定，不靠某一次升级的默认值。

错误按类型返回。业务错误带稳定的错误码，用 `errors.Is` 和 `errors.As` 判断。`fmt.Errorf("%w", err)` 包一层，调用方还能匹配到里面的哨兵错误。`errors.Join` 把多个错误收成一个，`errors.Is` 对里面每一个都有效；业务失败和清理失败要一起交出去时用它，只留后一个会把前一个吞掉。未捕获的异常由 Recovery 收成 500，日志里留栈，响应里不留栈。把内部错误的消息原样返回，会把 SQL 和路径暴露给调用方。业务上的「这笔订单不属于这个用户」在函数里显式返回 404。

请求 id 从网关的头读进来，没有就自己生成，放进 context。日志用 `slog` 的 `InfoContext`，从 context 里取出请求 id。下游调用继续传这个头。trace 的 `traceparent` 由 OpenTelemetry 的传播器写，和业务自己的请求 id 可以是同一个值，也可以并存。

后台任务如果用 `go` 启动，进程在它跑完前被杀掉，这个任务就没了。发邮件、写搜索索引这种必须做完的事放进消息队列。`go` 只用来做丢了也能接受的收尾，并且入口要接住 panic、使用脱离请求取消的 context，避免请求一结束就把收尾取消掉。`context.WithoutCancel` 能保留请求里的值，同时不受取消影响，超时要自己再设一个上限。

### 事务放在哪

GORM 的事务是一段函数，不是代理。调用必经这层函数，提交还是回滚看返回值。返回 `nil` 就提交，返回错误就回滚。没有「同一个结构体上的方法互相调用所以事务丢了」这层间接。要进事务的查询和写入，参数里传 `*gorm.DB` 的事务对象，不要在事务函数里又去用全局的 `db`，那条语句会落到另一条连接上。

```go
func (s *Service) Place(ctx context.Context, in Order) error {
    return s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
        if err := insertOrder(tx, in); err != nil {
            return err
        }
        return insertOutbox(tx, in)
    })
}
```

事务提交之后的动作放在 `Transaction` 返回 `nil` 之后：删缓存、唤醒一个内存里的通知。放在函数内部、返回之前，事务还可能失败，外部系统会先看到一次还没提交的变更。不要在事务函数里发 HTTP。行锁和连接会占到函数返回，外部调用一慢，池就空了。

隔离级别在 `sql.TxOptions` 里，于事务开始时设到这条连接上。只读事务不会自动走到从库，读副本用 `dbresolver` 或单独的库句柄。

### 和其他运行时的边界

Gin 和 `net/http` 是一个请求一个 goroutine，处理函数可以阻塞在数据库调用上，网络等待不会占住线程。CPU 密集的段落会占住 P。网关和长连接如果放在同一个进程里，给它们单独的超时和并发上限，不要和短请求抢同一个数据库池。

Kratos、go-zero、Hertz 把路由、注册、超时和代码生成收成一套。团队已经在那套治理上时，跟着那套走。这里的主路径是 Gin 加标准库服务器，注册和限流放在网关或 Nacos，避免一个进程里两套服务发现。

`chi` 和 `echo` 也是中间件模型，和 Gin 的差别主要在绑定和上下文的类型。标准库 `ServeMux` 够用时就不要再加一层。选框架看的是中间件、绑定和团队已经有的生成代码，不看谁的路由树在微基准里更快。路由匹配在整个请求里只占很小一段，时间花在数据库和下游上。

定时任务在每个副本上都会跑。必须只跑一份时，用数据库唯一键、租约，或者改成只在一个 Job 里跑。`time.Ticker` 的通道从 1.23 起是无缓冲的，`Stop` 和 `Reset` 与接收同步。1.27 去掉了退回旧行为的 `asynctimerchan`。循环里用 `NewTimer` 加 `Reset`，不要在循环里 `time.After`，哪怕短计时器现在可以被回收，重置一只已经在用的计时器仍然更清楚。

### 测试和运行

测试用表驱动。HTTP 用 `httptest.NewRecorder` 和 `httptest.NewRequest` 打处理函数，不先起端口。需要看真实超时和关闭时，用 `httptest.Server`。依赖换成接口的假实现。数据库测试用 testcontainers 起 MySQL，事务回滚让每个用例结束回到起点。SQL 方言的差异在集成测试里打真库。

`go test -race` 覆盖会碰共享状态的包。1.25 的 `testing/synctest` 可以在测试里控制时间和 goroutine 的推进，用来写超时和重试，不在测试里 `Sleep`。竞态和数据测试分开跑，`-race` 会把套件拉长。

解析、解码和一切「输入来自外面」的函数用 `go test -fuzz`。语料进 `testdata`，崩溃和超时会留下可以复现的输入。基准用 `go test -bench -benchmem -count`，多次结果交给 `benchstat` 比，单次数字会把噪声当成优化。`-benchmem` 里的 allocs/op 升高，先看是不是逃逸，而不是先改算法。

1.21 起，主包目录里有 `default.pgo` 时，`go build` 默认按这份 CPU profile 做 PGO（`-pgo=auto`）。它让热路径上的接口调用有机会改成直接调用，再进一步内联。profile 要来自生产或和图接近的压测，微基准的 profile 喂进去，线上几乎没有收益。profile 和源码一起进仓库，构建才能复现。不需要时用 `-pgo=off`。

模块用 `go.mod` 和 `go.sum`。`GOPROXY` 指向内部代理，`GOSUMDB` 保持校验，私有模块放进 `GOPRIVATE`，既不走公共代理，也不把路径发给校验和数据库。`govulncheck` 在流水线里看已经编进二进制的漏洞，不只看 `go.mod` 里写着的版本。

本地直接 `go run ./cmd/order`。生产用下一节的静态二进制，关掉 Gin 的调试模式。健康检查拆成存活和就绪，路径和 Kubernetes 探针那一节对上。访问日志打到 stdout。

---

## 6. 消息队列

队列用来解耦、异步和削峰。生产者写入后即可返回，消费者按自己的速度处理。下游停了，消息还在；队列自己停了，这条链路停。队列要单独做高可用。

本进程稍后执行、可以重试的任务用数据库任务表，或者绑在 Redis 上的任务库。要和别的服务交换业务事件，用 RabbitMQ、RocketMQ、Kafka 或 NATS JetStream，正文用 JSON。消息体里放业务键和版本，大文件放对象存储，消息里只放地址。

### 投递和幂等

默认是至少投递一次。超时后生产者会重发，消费者处理完却没确认，消息也会再来。消费逻辑按唯一业务键做成幂等。恰好一次的范围很窄：Kafka 可以把「写出下游消息」和「提交消费位点」放进同一个事务，这覆盖的是消费再转发。消费者写自己的数据库时，数据库事务和 Broker 的确认仍然是两步，业务侧按幂等来写。

幂等表和业务写在同一个事务里：

```sql
CREATE TABLE idempotency (
  idem_key VARCHAR(64) PRIMARY KEY,
  request_hash CHAR(64) NOT NULL,
  response_body JSON NULL,
  created_at DATETIME(3) NOT NULL
);
```

先插入幂等键，唯一键冲突就读出上一次的结果返回。同一个键配了不同的请求体，返回 409。订单表上的「用户 + 活动 + SKU」或请求 id 唯一键是同一件事的另一种落点。Redis `SET NX` 只能挡住短时间的重复，进程在写库前停了，键还在，请求会被挡住；键过期之后重复消息又会进来。持久的去重放在数据库。

本地事务和发消息放不到同一个数据库事务里。发件箱把这件事留在自己的库里：业务事务同时写入订单和一张 `outbox` 表，提交之后，另一个循环把未发送的行推到队列，成功再标成已发送。

```sql
CREATE TABLE outbox (
  id BIGINT PRIMARY KEY,
  aggregate_id BIGINT NOT NULL,
  topic VARCHAR(128) NOT NULL,
  payload JSON NOT NULL,
  status TINYINT NOT NULL,
  created_at DATETIME(3) NOT NULL,
  KEY idx_status_id (status, id)
);
```

进程在推送前停了，重启后会再推，所以消费者仍要幂等。轮询按 `status, id` 取一批，发送成功再更新状态。发送超时的行保持未发送。多个实例一起扫这张表时，用 `FOR UPDATE SKIP LOCKED` 把行分走，避免同一行被两个进程发出去。RocketMQ 的事务消息是 Broker 先收下一条消费者看不见的 half 消息，等本地事务结果再提交或回滚。发件箱不依赖某一种 Broker 的事务能力，换队列时业务事务不用改。

积压时先看是消费变慢还是生产变快。消费变慢就加消费者，或者先把非关键订阅停掉。毒消息是处理逻辑对这条正文永远失败。失败次数到顶后进死信，死信要有告警和人工或补偿消费。

四种队列的差别：

| | RabbitMQ | RocketMQ | Kafka | NATS JetStream |
| --- | --- | --- | --- | --- |
| 模型 | 交换机把消息推进队列，消费后从队列移除 | topic 下多个队列，Broker 存消息 | topic 下多个分区，追加日志，按保留时间删除 | subject 写进 stream。Limits 是有界日志；WorkQueue 是 ack 之后删除 |
| 顺序 | 单个队列里、单个消费者时 | 同一个消息组进同一个队列 | 同一个 key 进同一个分区 | 多个 worker 并发拉同一个消费者时，不按 subject 保持顺序 |
| 堆积 | 队列变长，堆积很大时更适合让 Kafka 来接 | 可以堆积，受磁盘限制 | 按磁盘保留，适合大流量和回放 | 受 `MaxMsgs`、`MaxBytes`、`MaxAge` 限制 |
| 消费语义 | ack 之前可以重投 | 不可见时间之后重新可见 | 位点没提交就会再投 | 显式 ack。`AckWait` 默认 30 秒，没应答就重投 |

### RabbitMQ

生产者只对交换机发，消费者只对队列收。队列没绑定交换机就收不到消息。客户端用 `amqp091-go`。连接可以跨 goroutine 使用，通道不行。一个 goroutine 一个通道。处理完再 `Ack`。失败则 `Nack`，并决定要不要重新入队。库本身不会在断线后把拓扑和消费者自动装回去，重连和重新声明要写在应用里，并且要有退避。

四种交换机。扇出复制到每一个绑定的队列。直连按完整的 routing key 匹配。主题用 `*` 匹配一个词、`#` 匹配零个或多个词。头交换机按消息头匹配。

publisher confirm 打开之后，Broker 接受消息才给回执。回调里按投递标签把发件箱那一行标成已发送。没开 confirm 时，`Publish` 返回只说明写进了客户端缓冲区。队列不存在且设置了 mandatory 时，走 return，发件箱那一行保持未发送。

死信有三条来源：队列满了并且拒绝新消息、消费者拒绝且不重新入队、消息超过 TTL。队列参数 `x-dead-letter-exchange` 和 `x-dead-letter-routing-key` 指向下一跳。仲裁队列还可以设 `x-delivery-limit`，同一条消息被投递到这个次数就进死信。用「每条消息一个 TTL + 死信」做延迟时，队列按队头检查过期，队头没到期，后面已经到期的消息也出不去。延迟各不相同就用延迟插件，或按延迟档位拆队列。

队列用仲裁队列（quorum queue）。副本按 Raft 多数派确认。镜像队列是旧的做法。prefetch 是每个消费者一次拿多少条未 ack 的消息。设得太大，消息堆在一个慢进程里，别的进程空闲。从 10 到 50 起，按处理时间调。一个队列上如果要求严格顺序，就只能有一个活跃消费者。确认要在数据库事务提交之后。先确认再提交，进程停在中间，这条消息就丢了。

### RocketMQ

4.x 的部署里，NameServer 保存 Broker 路由，生产者先问 NameServer，再写到 Master Broker 上对应 topic 的队列。5.x 的新客户端走 gRPC，连 Proxy，由 Proxy 找 Broker。Go 的 5.x 客户端在 `apache/rocketmq-clients` 的 golang 目录，4.x 的存量客户端是 `rocketmq-client-go`。两套协议的语义相同，客户端不能混在同一个调用栈里假装互通。

Producer 往 topic 发，Consumer 按组拉。组里的消费者分摊消息，组内订阅必须一致。广播是组里每个消费者都收到全量。消息上有 topic、tag、keys、body。tag 用来在订阅时过滤，keys 用来按业务 id 查。

发送有同步、异步、单向。单向不等确认，丢了也不知道。同步和异步要等 Broker 的响应。刷盘和复制可以是同步或异步，同步刷盘加同步复制才接近「响应返回时多数副本已经有了」。默认常常是异步刷盘，Broker 宕机会丢最后一小段。

集群消费下，消息有一段时间不可见。处理完必须 ack。超过不可见时间还没 ack，消息重新可见。失败让它稍后重投，次数到顶进入这个消费组的死信。死信要有人消费或告警。消费后的消息按保留时间删除，不是 ack 了就立刻从磁盘消失。

同一笔订单的创建、支付、取消用同一个消息组（sharding key），Broker 把它们放进同一个队列。顺序只在一个队列里成立。扩容之后，新的 key 才会落到新队列。顺序消费时，这个队列上同时只跑一个处理流程，不要再按消息启动不受限的 goroutine。

事务消息：先发 half 消息，消费者看不见；执行本地事务；再提交或回滚。进程在提交前停了，Broker 回查。回查做成无副作用的查询：本地事务表里有记录就提交，没有就回滚。回查可能发生多次。4.x 的延迟消息是固定的十几个档位，5.x 可以指定任意延迟时间。需要「30 分钟后关闭未支付订单」时，档位对不上就用时间轮或数据库扫描。

### Kafka

消息按 topic 再按 partition 追加，消费后不删，靠保留时间或大小清理。消费者属于一个组。同组一条消息只给一个消费者。另一组可以再消费全量。offset 提交了，同组就不会再投这条。分区可以回放，只要数据还在保留范围里。

纯 Go 的客户端用 `franz-go`。需要和 librdkafka 的行为严格对齐时再用带 cgo 的 confluent 客户端，那条路径要跟着 glibc 基础镜像走，不能进 `distroless/static`。它的默认分配策略已经是 cooperative-sticky。默认大约每 5 秒自动提交，提交的是上一次 `PollFetches` 拿到的位点：再 poll 一次，上一批才变成可提交。处理函数还没跑完就进入下一次 poll，上一批会被提交。要等数据库事务成功再前进，就关掉自动提交，处理完调用 `CommitRecords`。

```go
cl, err := kgo.NewClient(
    kgo.SeedBrokers("kafka:9092"),
    kgo.ConsumerGroup("inventory"),
    kgo.ConsumeTopics("order"),
    kgo.DisableAutoCommit(),
)
fetches := cl.PollFetches(ctx)
// 处理成功后再 CommitRecords；交出分区时在 OnPartitionsRevoked 里提交
```

两次 poll 的间隔有上限。处理太慢会被踢出组，分区分给别人。一次拉取的条数按处理速度设。处理函数里再同步调一个慢下游，很容易超过这个间隔。拉下来的这一批如果每条再 `go` 出去且不加限制，恢复时会把数据库池打满。并发度用信号量卡住，位点按这批里已经连续成功的前缀提交，不能只提交最大的那条而把前面失败的跳过。

新组从哪开始由 `auto.offset.reset` 决定。`latest` 只消费启动之后的新消息。`earliest` 从保留范围内最早的一条开始，之后仍接上次提交的位点。换 `group.id` 等于换一组新进度。

Rebalance 在组成员变化或分区数变化时发生。Range 按分区段切，RoundRobin 轮询，Sticky 尽量保住上次的分配。Cooperative Sticky 是增量再均衡：只收回要挪走的分区，其余分区继续消费。Range 和 RoundRobin 会让全组先停下来再分配。协调器是该组 offset 所在 `__consumer_offsets` 分区的 leader。再均衡期间，被收回的分区要停止拉取，已经在处理的消息要在交出分区之前结束，否则会和下一任消费者同时写。

消息 key 决定分区。同一订单号进同一分区，这个分区里才有顺序。key 太集中时，流量堆在一个分区上，加消费者也没用，因为一个分区同时只被组里的一个消费者读取。消费者数量超过分区数时，多出来的一直空闲。要提高并行度就先增加分区。分区数可以增加，减少会破坏 key 到分区的映射。

副本按分区组织，通常三个，落在三台 Broker 上。leader 负责这个分区的读写。follower 只做一件事：不停发 fetch，请求里带着下一条从哪个 offset 开始。leader 由此知道每个 follower 复制到了哪里。这和消费者拉消息是同一条批量读取路径。

同步复制要等副本写完才返回，最慢的那台会拖住所有写入。异步复制是 leader 写完就返回，还没复制出去的部分在 leader 宕机时丢掉。Raft 和 ZooKeeper 用多数派：超过半数确认，容忍 f 台故障要 2f+1 个副本。Kafka 维护的是 ISR，一份「跟得上日志末尾」的名单。follower 超过 `replica.lag.time.max.ms`（默认 30 秒）还没追上，就移出 ISR，补齐后再加回来。一条消息进入当前全部 ISR，才算已提交。落后的副本不会一直留在名单里拖慢确认。三副本、只从 ISR 接班时，坏两台仍不丢已提交的数据，因为最后一台曾经在 ISR 里的副本持有全部已提交消息。`min.insync.replicas=2` 时，坏一台还可以继续写；再坏一台，写入失败，直到副本回来。多数派同样三台只能坏一台。

每个副本有自己的 LEO（log end offset），指向下一条要写入的位置。HW（high watermark）是 leader 认为已经复制到全部 ISR 的位置。消费者只能读到 HW 之前的消息。leader 刚把一条消息写在 offset 500，LEO 变成 501，follower 还没拉走时 HW 仍停在 500，消费者最多读到 499。ISR 里的 follower 都发来「从 501 开始拉」，leader 才把 HW 推进到 501。消费者读到的消息不会因为 leader 宕机凭空消失。

生产者用 `acks` 选择等多少确认。`acks=0` 发出去就算成功。`acks=1` 是 leader 写完就返回，这是异步复制，leader 在复制前宕机会丢掉已经返回成功的消息。`acks=all` 要等消息进入当前全部 ISR。生产配置里写上 `acks=all` 并打开幂等，不靠记忆中的客户端默认。Java 客户端从 Kafka 3.0 起这两项已经是默认；`franz-go` 按它自己的配置走。幂等生产者用生产者 id 加分区内序列号，在 Broker 端去掉同一次会话里的重试重复。

`acks=all` 等的是当时的 ISR，不是主题上配置的全部副本。ISR 可以缩到只剩 leader，这时 `acks=all` 也只写了一份。主题的 `min.insync.replicas` 至少为 2，副本因子至少 3。这项的默认是 1。ISR 数量不够时，生产者收到 `NotEnoughReplicas`，写入失败。`unclean.leader.election.enable` 保持关闭。ISR 空了就等待，不让落后副本接班。

控制器先从非空的 ISR 里选新 leader。Kafka 4.0 起可以启用 ELR（合格主副本，KIP-966）：不在 ISR 里、但日志至少覆盖到已提交高水位的副本，也能当接班人。它只在 KRaft 上生效。4.0 默认关闭，要设 `eligible.leader.replicas.version=1`。4.1 起新建集群默认开启，已经在跑的集群升级到 4.1 不会自动打开。打开之后，`min.insync.replicas` 放在集群或主题上，Broker 级的这项配置不再单独生效。选举顺序是：非空的 ISR，然后未被隔离的 ELR，最后才是上一次的 leader，而且它也不能处于隔离状态。ELR 接班的副本至少持有已提交的数据。unclean 选举是另一条路，会让没有这份数据的副本接班，已确认的消息会被截掉。

旧 leader 恢复后，日志尾巴上可能有从未提交、和新 leader 不一致的记录。每一任 leader 有递增的 leader epoch，日志记下这一任从哪个 offset 开始。旧 leader 先问新 leader 自己这一任到哪结束，截掉分叉点之后的日志，再把正确的数据拉回来。截掉的是超过前任 HW、从未对消费者可见的部分。

这套设计省在副本数量：f+1 份就能在 f 台故障下保住已提交数据，比多数派少占磁盘。`acks` 和 `min.insync.replicas` 按主题配置，埋点可以追求速度，扣款把两项都拧紧。follower 批量拉取，复制走和消费者同一条读路径。代价也明确：`acks=all` 的延迟取决于 ISR 里最慢的副本，跨机房更明显；下限设高了，ISR 一收缩写入就失败，设成 1 又会在只剩 leader 时确认成功；三副本就是大约三倍的磁盘和复制带宽；一个分区的写入只打 leader，2.4 起消费者可以就近读 follower，热点分区的 leader 仍然是写入的压力点。为了快点恢复而打开 unclean 选举，就要接受已确认消息被截掉。

扣款、借书这种丢一条就要对账的主题，生产配置是三副本、`acks=all`、`min.insync.replicas=2`、关闭 unclean 选举。任意一台宕机，已确认的消息还在，其余副本仍能写入。消费者处理成功但提交位点失败，仍会再投一次。失败次数到顶后发到死信主题，位点不要向前跳过失败的那条。

积压看 lag：分区末端位点减去已提交位点。先分辨是处理变慢还是生产变快。扩容消费者之前先确认还有空闲分区。按 lag 扩容用 KEDA，HPA 的 CPU 反映不出堆积。

### NATS 与 JetStream

NATS 是一套纯 Go 的主题系统，客户端是 `nats.go`。它分成两层，两层的投递承诺不一样。

核心 NATS 不把消息落盘。发布返回，只说明到了服务器；当时没有订阅者，消息就没了。慢的订阅者会被断开，服务器不会为它把消息堆在磁盘上。队列组让一组订阅者竞争同一条消息，仍然没有持久化，进程停了，还没处理的就丢了。请求-应答用一个临时的收件主题把响应送回来，调用方设超时。它适合延迟很短的内部询问，不代替订单路径上的 gRPC，也不当发件箱。

JetStream 在主题上面加一条流。生产用文件存储，内存存储跟服务器进程一起消失。集群里一条流按配置的副本数走 Raft，发布者收到成功时，这条消息已经进了多数派。副本数用 3。客户端用 `jetstream.New`，消费者用 `CreateOrUpdateConsumer`，处理走 pull：`Consume` 或 `Messages`。push 还在，是为了迁旧代码。

流的保留策略决定消息什么时候可以走。三条里只能选一条，限额在三条上都会生效。

| 策略 | 消息何时离开 | 适合 | 会怎么坏 |
| --- | --- | --- | --- |
| Limits，默认 | 碰到 `MaxMsgs`、`MaxBytes` 或 `MaxAge` | 要回放的事件，和 Kafka 的保留窗口同类 | ack 了也不删。窗口太长会把磁盘占满 |
| Interest | 每一个关心这条 subject 的消费者都 ack 之后 | 几个服务都要处理一次、处理完就可以忘 | 一个慢消费者把所有消息留住，直到碰上限额 |
| WorkQueue | 第一个 ack 就从流里删掉 | 一件工作只交给一个工人 | 消息 ack 之后不能再回放。新来的消费者看不到已经做完的 |

Interest 上如果还没有任何消费者的过滤盖住这条 subject，消息会被马上删掉。消费者必须先建好再发布。WorkQueue 上的多个消费者，过滤的 subject 不能重叠。两个工人要抢同一批 subject，就建一个消费者，由它并发拉取。建两个过滤相同的消费者，服务器会拒绝。搜索和库存都要看「订单已创建」时，用 Limits，各建一个持久消费者。这和 Kafka 里两个消费组各有一份位点是同一件事。用 WorkQueue 会让先 ack 的那个把另一个的消息删掉。

消费者的进度记在持久名字上。换一个名字等于换一组新进度。新消费者的投递策略默认是 `all`：从流里还留着的第一条开始。只要新消息时写成 `new`。这和 Kafka 默认从 latest 开始相反，接上去就会把整个保留窗口再处理一遍。

应答默认按显式 ack 来写。`AckWait` 默认 30 秒，这段时间里没有 ack、没有 nak、也没有 `InProgress`，服务器就再投一次。`MaxDeliver` 默认 `-1`，会一直重投。处理超过 30 秒就先 `InProgress` 续上计时，或者把 `AckWait` 放到比数据库事务上限更长。`Nak` 会立刻重投，消费者上配置的退避间隔只作用于 `AckWait` 超时，不作用于一次裸的 `Nak`。要推迟就用 `NakWithDelay`。

JetStream 没有内置死信主题。`MaxDeliver` 到了，这条消息从该消费者的待投递里拿掉，不再投给它，消息本身还在 Limits 流里。服务器同时往 `$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.<stream>.<consumer>` 发一条咨询。要留住毒消息，就订阅这条咨询，或者在最后一次失败时自己发布到死信 subject，再用 `Term` 告诉服务器这个消费者不要再试。没人订阅咨询、也没有 `Term` 之前的记录，毒消息就是静默停在流里。

同一个消费者上并发拉取时，两条相同 subject 的消息可以落到两个 worker 上。顺序不会按 subject 自动保持。`MaxAckPending` 默认 1000，限制的是还没 ack 的条数，不是顺序。要严格按流的顺序处理，用有序消费者：它不 ack，发现序号有缺口就从缺口重建，适合回放和巡检，不适合「数据库提交之后再确认」。要按订单号顺序做有副作用的处理，就把订单号编进 subject，一个消费者、`MaxAckPending` 设成 1，或者接受并发然后靠数据库唯一键。

```go
cons, err := js.CreateOrUpdateConsumer(ctx, "ORDERS", jetstream.ConsumerConfig{
    Durable:       "inventory",
    AckPolicy:     jetstream.AckExplicitPolicy,
    FilterSubject: "orders.created",
    DeliverPolicy: jetstream.DeliverNewPolicy,
    MaxDeliver:    5,
    AckWait:       30 * time.Second,
})
cc, err := cons.Consume(func(msg jetstream.Msg) {
    if err := handle(msg.Data()); err != nil {
        _ = msg.NakWithDelay(time.Second)
        return
    }
    _ = msg.DoubleAck(context.Background())
})
defer cc.Stop()
```

`handle` 里先提交自己的数据库事务。`DoubleAck` 等服务器确认这次 ack。`Ack` 写进客户端缓冲就返回，进程停在中间时，`AckWait` 到了会重投。重投仍然靠前面的幂等表。

发件箱要被多个下游各自回放、保留以天计，继续用 Kafka 的分区日志。JetStream 的 Limits 能覆盖「留一个窗口、几个消费者各处理一次」。核心 NATS 覆盖不了这两件事。

---

## 7. Docker 与 Kubernetes

容器把进程放进独立的 namespace，用 cgroup 限制资源，用镜像里的根文件系统启动。Kubernetes 在多台机器上调度这些容器，做自愈、滚动升级和服务发现。Go 服务要额外处理静态链接、cgroup 里的 `GOMAXPROCS`，以及镜像里没有 shell 时信号和探针怎么做。

### 镜像

镜像是只读层叠起来的。容器的写入落在最上面的可写层，容器删了可写层就没了。要留的数据用卷。层是按 Dockerfile 的每一条生成文件的指令缓存的。依赖很少变，代码经常变，所以先拷 `go.mod` 和 `go.sum` 下载模块，最后才拷源码。改一行业务代码不会重新下载依赖。

`CGO_ENABLED=0` 打出来的是不依赖 libc 的静态二进制，可以放进 `distroless/static` 或 scratch。默认打开 cgo 时，二进制动态链接 glibc，拷到 Alpine 上会起不来，因为 Alpine 用的是 musl。需要 cgo 的依赖（例如 librdkafka）就改用带 glibc 的基础镜像，并把 C 库的运行时留在最终镜像里。纯 Go 的 DNS 解析在 `CGO_ENABLED=0` 时生效，企业网里如果依赖 nsswitch 的特殊解析，要在预发验证一次名字能解析到。

```dockerfile
FROM golang:1.27 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/app ./cmd/order

FROM gcr.io/distroless/static-debian12
COPY --from=build /out/app /app
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/app"]
```

`distroless/static` 带 CA 证书和时区数据，没有 shell。scratch 连证书都没有，访问 HTTPS 会在校验证书时失败。`USER` 放在入口前面，进程不要用 root。拷文件用 `COPY`。`ADD` 还会拉 URL、解压缩包，构建结果会跟着网络变。`EXPOSE` 只是文档。exec 形式让二进制成为 PID 1，才能收到 `SIGTERM`。镜像里没有 shell，健康检查不要写 `curl`，由 kubelet 直接打 HTTP。`.dockerignore` 去掉 `.git`、本地 `.env` 和测试数据。镜像标签用 git 提交号。生产不用 `latest` 作为唯一标识。

日志打到 stdout。需要留存的上传文件进对象存储。只读根文件系统上，真正要写的临时目录用 emptyDir。pprof 的导出文件写到这块盘上，避免写进可写层把节点磁盘打满。

Compose 把应用、MySQL、Redis、队列写在一份 YAML 里，给本地和测试用。服务之间用服务名当主机名。`depends_on` 只保证启动顺序。数据库写 `healthcheck`，应用用 `condition: service_healthy`。健康检查通过只说明端口开了，迁移是否跑完由应用自己重试。生产不用 `network_mode: host`。

### 集群里有什么

控制面有四个组件。API Server 是唯一入口，所有写操作经过它。etcd 存集群状态。调度器给还没有节点的 Pod 选一台机器，依据是资源请求、亲和、污点和拓扑。Controller Manager 里跑各种控制器，它们把当前状态往 spec 上推：副本少了就建，节点没了就把 Pod 排走。

节点上有 kubelet 和容器运行时。kubelet 按 Pod 清单拉镜像、起容器、执行探针，停机时先跑 preStop，再发 `SIGTERM`。kube-proxy 监听 Service 和 EndpointSlice，用 iptables、IPVS 或 eBPF 把 ClusterIP 转成 Pod IP。CoreDNS 把服务名解析成 ClusterIP。

Pod 是调度的最小单位。同一个 Pod 里的容器共享网络 namespace 和存储卷，用 localhost 加端口互相访问。一个 Pod 里放主进程和必须跟它同生共死的边车。可以分开扩缩的进程分成不同的 Deployment。

工作负载按状态来选：

| 对象 | 用途 |
| --- | --- |
| Deployment | 无状态服务。Pod 可替换，用滚动更新 |
| StatefulSet | 稳定的网络名和每副本一块盘。有序滚动。消息队列和数据库用它，业务服务通常不用 |
| DaemonSet | 每个节点一份。日志采集和网络代理 |
| Job / CronJob | 跑完就结束的任务。迁移和对账 |
| ReplicaSet | 维持副本数。一般由 Deployment 管，不单独写 |

Service 按标签选 Pod，提供一个稳定的虚拟 IP。四种类型：`ClusterIP` 只在集群内；`NodePort` 在每个节点上开一个高位端口；`LoadBalancer` 让云创建外部负载均衡；`ExternalName` 返回一条 CNAME。应用只连服务的 DNS 名。Endpoints 为空时，Service 有 IP 但转不走，调用方看到连接失败，先查标签和就绪探针。

Ingress 是七层入口，按 Host 和路径转到 Service。证书放在 Ingress 上。Gateway API 是后出现的模型，把网关的部署和路由规则拆开。NetworkPolicy 限制哪些 Pod 可以互相访问。默认经常是全通，生产按命名空间收成「入口来自网关，数据层只接受应用命名空间」。

配置进 ConfigMap，密钥进 Secret。密钥不写进镜像层，也不进 Git。只改 ConfigMap 不会重启容器，进程已经读进内存的值也不会变。需要整进程重载时，给 Pod 模板加一个配置摘要注解，摘要变了就滚动。数据源地址变了必须重建池，跟着滚动更稳妥。

### 控制器

Controller Manager 里的控制器，和业务自己写的 Operator，是同一个循环：读期望，看现状，补上差的那一块。再跑一遍如果已经对齐，就什么都不做。订单服务不是这种进程。订单的期望在 MySQL 的行里，用事务和发件箱往前推。控制器的期望在 API Server 里，etcd 存的是集群对象，不存订单行。要写控制器的场合，是期望本身就是一个 CRD：把一份自定义对象落实成 Deployment、Service 或云上的资源。

触发用水平，不用边沿。「收到一次更新就做一次副作用」会在事件丢失或重复时漏掉。调谐函数每次从缓存读出现在的对象，算出世界应该变成什么样，只补这一次的差。函数要能重复执行。

一条对象从 API Server 到调谐函数，经过四段：

1. Reflector 先 List，再从这一次的 `resourceVersion` 接着 Watch。Watch 断了就重新 List。
2. DeltaFIFO 按对象的键把变化积起来。类型有 Added、Updated、Deleted、Sync。一次 Pop 拿到的是上次处理之后发生的这一串，不是一条容量为 1 的通道里被挤掉的单个事件。
3. Informer 把变化写进本地索引，再通知处理函数。Lister 读的是这个索引，不是 API Server。缓存比集群晚一拍。同一个进程里一种资源只建一个 SharedInformer，很多控制器挂在同一次 Watch 上。每个请求新建一个 Watch，会把 API Server 打满。
4. 处理函数只把键放进工作队列。键是 `命名空间/名字`。删除事件如果已经赶不上最后状态，对象会包在 `DeletedFinalStateUnknown` 里，用 `DeletionHandlingMetaNamespaceKeyFunc` 仍能取出键。在 `OnAdd` 里直接调谐，用的是事件里那份已经过时的对象。

工作队列里是三份集合。dirty 是还需要对齐的键。processing 是正被某个 worker 拿着的键。queue 是可以取走的顺序。同一个键加一百次，队列里只有一条。worker 正拿着它时又来了新事件，不会再开一个 worker，`Done` 的时候再放回队列。所以一个键同时只有一个调谐在跑，更新风暴会被收成「再对齐一次」。

```go
func (c *Controller) process(key string) {
    defer c.queue.Done(key)
    obj, exists, err := c.indexer.GetByKey(key)
    if err != nil {
        c.queue.AddRateLimited(key)
        return
    }
    if !exists {
        c.queue.Forget(key)
        return
    }
    if err := c.reconcile(obj); err != nil {
        c.queue.AddRateLimited(key)
        return
    }
    c.queue.Forget(key)
}
```

`GetByKey` 拿到的是索引里的最新对象。成功时 `Forget` 把这个键的退避清零，下一次失败从最短的间隔重新算。失败时 `AddRateLimited`，间隔按这个键指数拉长。`Done` 放在 `defer` 里，包含 panic 被接住的路径。漏了 `Done`，键一直留在 processing 里，后面的事件都不会再被取出来。`AddRateLimited` 发生在 `Done` 之前：键还在 processing 中，只是被标回 dirty，`Done` 时入队一次。

resync 周期走的是本地索引，不会向 API Server 再要一份全量。它给还没在队列里的键补一条 Sync。处理函数看到的更新，旧对象和新对象可以是同一个。处理函数仍然只入队。这个周期是「刚才的 `Done` 漏了、或者处理函数当时出错」的安全网，不是刷新 etcd。`resyncPeriod` 为 0 就没有这张网。对账若要按自己的节奏做，在调谐里 `AddAfter`。

`WaitForCacheSync` 之前不要调谐。初始 List 还没进索引时，缓存是空的，按「对象不存在」去删真实资源，会把集群清掉。

副本跑两个是为了进程挂了还有人接。同时写的只能有一个。选主用 `coordination.k8s.io/v1` 的 Lease：持有者在 `renewTime` 之前续约，`leaseDurationSeconds` 过去之后别人才能拿走。只有领导者启动工作队列。失去领导时，先把 worker 停干净，再取消租约。官方示例要求被租约保护的循环在取消之前结束。先放租约、worker 还在写，下一任已经开始，两个人一起调谐。

这把租约没有 fencing token。旧进程在租约过期之后、自己发现之前，仍可能发出 `Update`。挡住它的是对象上的 `resourceVersion`：拿着旧版本去更新，API Server 拒绝，处理函数重新 Get 再算。租约负责「尽量只有一个写者」，`resourceVersion` 负责「过期的写入失败」。

`controller-runtime` 把上面这套收成 `Reconcile(ctx, Request) error`。返回错误会按退避重新入队，返回 nil 就结束这一轮。要看见队列和索引时用 client-go，要写一个 CRD 的 Operator 时用这一层。两种都不要进订单 API 的进程。

### 持久卷

容器的可写层和 `emptyDir` 随 Pod 一起消失。`emptyDir` 适合临时文件和 pprof 导出。`hostPath` 把节点上的目录露进容器，Pod 换一台机器就找不到原来的文件，也把节点文件系统暴露给进程。业务数据不放这两种。

要活过 Pod 重建的磁盘分成三层。PersistentVolume 是集群里的一块存储。PersistentVolumeClaim 是 Pod 发出的申请，写容量、访问模式和 StorageClass。StorageClass 上的供给器看到申请后，去云盘或存储系统里把 PV 造出来并绑上。应用清单里只写 PVC，不写某块盘的 id。

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: order-data
spec:
  accessModes: ["ReadWriteOnce"]
  storageClassName: ssd
  resources:
    requests:
      storage: 20Gi
```

访问模式是约束。`ReadWriteOnce` 表示同一时刻只能挂到一个节点上读写。两个副本的 Deployment 共用一块这样的盘，后调度到别的节点的那个 Pod 会一直 Pending。`ReadOnlyMany` 可以多节点只读。`ReadWriteMany` 可以多节点读写，底层得是共享文件系统，普通云盘做不到。数据库和队列用一副本一块 `ReadWriteOnce` 盘，由 StatefulSet 的 `volumeClaimTemplates` 按序号各申请一块，Pod 重建后还挂原来那块。

供给时机用 `volumeBindingMode: WaitForFirstConsumer`。立刻供给会在申请创建时就把盘造在某个可用区，调度器随后可能把 Pod 放到另一个区。等 Pod 选好节点再造盘，盘和 Pod 在同一个区。

回收策略：`Delete` 在 PVC 删除时连带删掉云盘。`Retain` 留下云盘和数据。生产数据盘用 `Retain`，确认备份之后再手工删。删 Deployment 不会删 StatefulSet 管的 PVC。

磁盘快照是崩溃一致性，MySQL 可能正写着一页。库的备份仍用 `xtrabackup` 或带 GTID 的逻辑备份。非 root 用户要能写挂载点，Pod 的 `securityContext.fsGroup` 设成镜像里那个用户的组。`distroless` 的 `nonroot` 是 65532，fsGroup 和它对上。

Go 服务自己无状态。上传文件进对象存储。PVC 留给自建的数据库、队列和必须落在磁盘上的大文件。托管的 RDS 和 Redis 不需要在业务 Pod 上挂盘。

### 探针、资源和发布

Go 进程启动通常比带 JIT 的运行时快，但建连、迁移检查和本地缓存预热仍要时间。`startupProbe` 给足失败次数，过了启动阶段才开始存活探针。就绪探针失败只是从 Service 摘掉，不重启。存活探针打 `/healthz`，只判断进程还能不能回答。就绪探针打 `/readyz`。数据库、Redis 只放进就绪。放进存活的话，依赖一抖动，kubelet 会把 Pod 全部重启。

```yaml
startupProbe:
  httpGet: { path: /healthz, port: 8080 }
  periodSeconds: 5
  failureThreshold: 30
readinessProbe:
  httpGet: { path: /readyz, port: 8080 }
  periodSeconds: 5
livenessProbe:
  httpGet: { path: /healthz, port: 8080 }
  periodSeconds: 10
```

内存按 `GOMEMLIMIT` 加上栈、cgo 和页缓存之外的余量估算，写进 `resources.requests` 和 `limits`。`GOMEMLIMIT` 设在 limit 的八到九成。CPU 的 limit 会触发节流。1.25 起 `GOMAXPROCS` 会跟着 limit 走，GC 不再按整机核数把配额打满；limit 仍然是吞吐量上限，突发会被 P 的个数卡住。IO 型的服务把 CPU limit 设得明显高于 request，或者只设 request、用命名空间的 LimitRange 兜住上限。内存 limit 仍然要设。

QoS 按 requests 和 limits 的关系分：两者相等是 Guaranteed，只设了 requests 或两者不等是 Burstable，都没设是 BestEffort。节点内存紧张时，BestEffort 先被杀，然后是超出 requests 的 Burstable。

HPA 看 CPU 时，进程经常在等数据库，CPU 很低但连接池已经满了。更有用的是正在处理的请求数、`database/sql` 的等待时间、Redis 池的占用。这些做成自定义指标再交给 HPA。队列堆积用 KEDA 按 lag 扩消费者。堆使用率不适合当扩容信号，GC 之后 RSS 也不会立刻回到零。goroutine 数可以当饱和度的参考，它涨往往是下游变慢，扩容之前先确认下游还能接。

滚动更新用 `maxUnavailable: 0`、`maxSurge: 1`：先起一个新 Pod，就绪之后再停一个旧的。`minReadySeconds` 设几秒，避免刚通过探针就接全量流量。`PodDisruptionBudget` 的 `minAvailable` 保证腾挪节点时至少留一个能服务的 Pod。副本数是 1 时，PDB 和自愿驱逐会互相卡住，至少两个副本再加 PDB。

Pod 进入 Terminating 之后，端点摘除和 `preStop` 并行发生。摘除要等 kube-proxy 或 eBPF 刷新，这段时间新请求还会进来。`preStop` 里先等几秒，再让进程处理 `SIGTERM`。镜像里没有 shell，不能写 `sh -c sleep`。用生命周期自带的 sleep：

```yaml
lifecycle:
  preStop:
    sleep:
      seconds: 10
terminationGracePeriodSeconds: 45
```

`terminationGracePeriodSeconds` 从停机流程开始算，到点还没退出就 `SIGKILL`。preStop 的等待加上 `Shutdown` 的超时要小于这个值。默认宽限 30 秒，preStop 睡 10 秒、优雅停机再要 30 秒，就会在事务还没结束时被强杀。

镜像拉取用 `imagePullPolicy: IfNotPresent` 配合不可变标签。拉取密钥放在 ServiceAccount 上。

---

## 8. 微服务

微服务是按业务能力拆开的一组进程：各自部署，各自拥有数据，通过网络调用或消息协作。拆开之后，故障面变小，发布可以分开。代价是一次业务操作跨过网络，事务、超时和观测都要单独设计。系统不够大时，一个仓库里的包边界就够了。

### 边界

代码上对应三种边界：

- 一个进程里的包：`account`、`order` 只是目录，调用是函数调用。事务还是本地的。
- 独立进程：各自的镜像、Deployment、数据库。调用走 HTTP、gRPC 或消息。一个进程停了，另一个进程还在。
- 内部模块：发到内部代理，各服务钉死主版本。模块里放日志格式、追踪上下文、错误码。创建订单这种业务留在订单服务里。

按业务能力拆，不按技术层拆。把「所有数据库访问」放进一个服务、把「所有校验」放进另一个服务，一次请求仍然要穿过所有层，发布还得一起发。订单服务拥有订单表，商品服务拥有商品表。需要别人的数据时，要么同步调用拿一次，要么订阅对方的事件在本地留一份只读副本。两个服务写同一张表，就回到了共享数据库。

中台是把各业务线里重复的能力抽出来。业务中台是用户、会员、支付、权限这种各条线都要的服务。数据中台是统一的存储、建模和计算。技术中台是日志、配置、鉴权、任务这些共用平台。烟囱是每条产品线各做一份权限和订单。落地顺序是：先按业务拆开，再把共有能力收成平台，最后调整编制和投入。调用方拿用户 id 就能完成登录态校验，用户服务把存储细节留在自己这边。

拆分的信号是：这个模块要独立扩容，发布节奏和别人不同，故障时不想拖垮主路径，数据所有权已经清楚。反向的信号是：每次改需求都要改三个服务，同步调用链超过三跳，为了一次查询做分布式事务。这种情况下先把边界收回到一个进程里。

进程之间有两条常用的调用方式。HTTP 加 JSON 用进程级的 `http.Client`，网关、外部伙伴和语言不同的调用方都走它。内部、字段稳定、调用频繁时用 gRPC。对外的 API 仍留在 HTTP。

### 同步调用上的保护

服务之间用一个进程级的客户端。超时拆开，重试只打在幂等请求上。超时从外往里缩短。

这些保护要一起用：

- 超时：每次下游调用都有上限，拨号、读响应头、整次调用分开。上下文的截止时间从入口的剩余时间往下传，不再用自己原来的一整段。
- 重试：有次数、有抖动，只打幂等接口。重试会放大流量。
- 熔断：连续失败或慢调用比例过高之后暂时不再调用。半开时放少量探测。打开期间返回缓存里的旧数据，或一个明确的稍后再试。
- 舱壁：每个下游一把 `semaphore.Weighted` 和一份 `MaxConnsPerHost`。支付慢只占满支付的额度，查询还剩自己的连接。
- 限流：在入口按用户和接口收住，在服务里按自己的容量再收一层。`golang.org/x/time/rate` 是进程内的令牌桶。
- 过载拒绝：正在处理的请求数到了上限就直接 503，带 `Retry-After`，不再排队。

Go 没有固定大小的工作线程池。请求一多，goroutine 会一直创建，然后堵在借数据库连接上。延迟已经变高时还继续排队，在途请求按「并发 ≈ 到达率 × 停留时间」继续涨，把后面的请求一起拖死。上限按连接池和可接受的等待时间来定，不按机器还能再开多少 goroutine 来定。

计数在进程内时，每个副本各算各的。入口限流放在网关，效果对所有副本同时生效。分布式的令牌桶放在 Redis，用一段 Lua 做完取令牌。进程内的桶只挡突发，不代替全局配额。

sentinel-golang 可以把流控、熔断和热点参数收成规则，和 Java 侧的 Sentinel 是同一套概念。已经在用那套规则时再引入。网关的限流和服务里的信号量同时存在是正常的，两套熔断同时对同一个下游计数，阈值会对不上，只留一处。

幂等键由调用方生成，放在头里，服务端用前面的幂等表记住结果。超时之后调用方用同一个键再请求，拿到的是第一次的响应。键要跟着业务单走，用户每点一次「提交」是一个新键，同一次提交的自动重试是同一个键。

```go
func (c *PayClient) Pay(ctx context.Context, key string, body []byte) (*http.Response, error) {
    if err := c.sem.Acquire(ctx, 1); err != nil {
        return nil, err
    }
    defer c.sem.Release(1)
    req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+"/pay", bytes.NewReader(body))
    if err != nil {
        return nil, err
    }
    req.Header.Set("Idempotency-Key", key)
    return c.client.Do(req)
}
```

服务网格把 mTLS、部分重试和指标下沉到边车。应用仍然要设自己的超时和幂等。边车的重试和代码里的重试叠在一起，总次数是相乘的。开了网格之后，重试只留一处。

### 跨服务的数据

一次下单要改订单、库存和优惠券。这三个库没有同一个本地事务。做法按一致性要求选：

- 能放在一个库里的，就还是一个库、一个事务。拆服务不强制拆库。
- 发件箱加事件。订单事务提交后，库存服务消费事件再扣。中间有一段时间两边不一致，用对账补。
- Saga 是一串本地事务加补偿。编排式由一个协调者按步骤调用，失败了按相反顺序执行补偿。编舞式由每个服务发出事件，下一个服务自己接着做。补偿要做成幂等。Saga 没有隔离性：步骤之间别人能看见中间状态，业务上要用状态机把「支付中」「已取消」显式写出来。
- TCC 把每个资源分成 Try、Confirm、Cancel。Try 预留，Confirm 确认，Cancel 释放。隔离性比 Saga 好，三个阶段都要写。空回滚是 Cancel 比 Try 先到，Cancel 要能当成无事发生。悬挂是 Cancel 之后迟到的 Try，Try 要拒绝。每个阶段按事务 id 幂等。

两阶段提交让所有参与者先准备再提交。协调者在准备之后宕机，参与者会一直占着锁等它。跨服务的业务事务不用数据库的 XA 来包 HTTP。InnoDB 内部 redo 和 binlog 的两阶段是一个进程里的事。

读自己的写入：写完去从库或去别人的只读副本，可能还没有这笔数据。用户刚提交的订单，查询打主库或打本地已提交的结果。列表和搜索可以接受秒级延迟。

接口演进用加字段、不改旧字段含义的方式。先让调用方能忽略新字段，再让调用方开始写新字段，最后才停用旧字段。同时在线的两个版本要都能处理对方的消息。消息体里带 schema 版本。protobuf 的字段号同样遵守这条：只加不改、不复用。破坏性变更用新的路径或新的 topic。

### 网关、配置与发现

鉴权、限流、黑名单放在入口，每个服务就不用各写一份。APISIX 或 Kong 放在 Pod 前面：

- 路由：按路径或 Host 转到哪个上游。上游是一组 Pod 的 Service。
- 匹配：路径前缀、Header、方法。不满足就不转发。
- 插件：JWT、限流、改写路径、请求 id、访问日志。

只有一个服务、流量也不大时，在 Gin 的中间件里做鉴权。服务多了之后，令牌校验放到网关，后面的服务信任网关注入的用户 id。这个头在最外层入口清掉再由网关写入，调用方自己带的同名头进不了内网。网关做所有服务都要的事：TLS、限流、鉴权、请求 id、访问日志、转发。进程里留和这个资源有关的事：参数校验、这笔订单是不是这个用户的、幂等。网关不知道订单属主。

### 登录态

浏览器应用用服务端会话。登录成功后在 Redis 里写一份会话，Cookie 里只放会话 id。Cookie 设 `HttpOnly`、`Secure`，`SameSite=Lax`。`HttpOnly` 让页面脚本读不到它。注销就是删掉 Redis 里的这份会话，所有副本立刻失效。改权限也是改这一份，下一次请求就生效。

Cookie 会由浏览器自动带上。`SameSite=Lax` 挡住别的站点发起的 POST，顶层链接进来的 GET 仍会带上。会改数据的接口再对一份 CSRF 令牌，不把「带了 Cookie」当成唯一的授权依据。

JWT 把声明和签名放在令牌里，网关用密钥验签，不必每次去 Redis。令牌是三段 base64url：头、载荷、签名。载荷里放 `sub`（用户 id）、`exp`、`iat`、`iss`、`aud`、`jti`。角色可以放，但权限一改，旧令牌里的角色还要等到过期。密码、手机号、完整资料不放进去，令牌会进日志和浏览器。

签名算法在服务端固定。验签时不采用令牌头里临时指定的算法，避免被换成 `none` 或换成攻击者自己的密钥。`golang-jwt` 用 `WithValidMethods` 把算法收成允许的那一种。对称的 HS256 要求所有验签的服务共享密钥。非对称的 RS256 或 EdDSA 由认证服务用私钥签，网关只用公钥验，私钥不进业务服务。

访问令牌的有效期用几分钟到一刻钟。刷新令牌有效期长，只交给认证服务，在 Redis 或数据库里存它的哈希。换发时作废旧的刷新令牌、发新的一对。旧的刷新令牌再次出现，就把这一族都作废，当成已经泄露。注销时删掉刷新令牌，并把访问令牌的 `jti` 放进拒绝列表，放到它的 `exp` 为止。访问令牌越短，拒绝列表越小。

浏览器里，访问令牌放内存，刷新令牌放 `HttpOnly` Cookie。放进 `localStorage` 的令牌，页面上任何一段脚本都能读走。手机和别的服务用 `Authorization: Bearer`。网关验完签，把用户 id 写进内部头。业务服务查订单时用这个 id 做条件，不信任路径或正文里自带的用户 id。

时钟有偏差，`exp` 的校验留几十秒余量。余量只盖住时钟误差，不拿它延长令牌寿命。

限流按路由配。秒杀和普通查询分开。固定窗口会在窗口边界放进两倍流量。漏桶按固定速率漏出。令牌桶允许短时间的突发，桶空了就拒绝。限流键用用户 id，没有登录态时用 IP。只按 IP 会误伤出口地址相同的一群人。分布式限流的计数放在 Redis，用一段 Lua 做令牌桶或滑动窗口。

Nacos 同时做注册和配置。纯 Kubernetes 用 Service 和 CoreDNS 发现，配置用 ConfigMap。虚拟机或混部再用 Nacos。两套发现会给出不同的地址，选一套。

`nacos-sdk-go` v2 走 gRPC，端口是 HTTP 端口加 1000，8848 旁边是 9848。Data Id 用「应用名-环境.后缀」。共享配置和扩展配置分成不同 Data Id，后读到的覆盖先读到的。命名空间或分组写错时读到空内容。监听回调里替换进程内的配置对象。已经建立的数据库连接池不会因为配置文本变了就自动换主机，换地址要重建 `*sql.DB`。

回调不一定跑在处理请求的 goroutine 上。回调里只解析文本、换上一个不可变配置对象。解析失败保持旧配置。本地缓存目录在连不上服务器时用来启动。容器根文件系统只读时，把这个目录指到可写的 emptyDir。

注册用临时实例：心跳在，实例就在；心跳没了，服务器超时后摘掉。持久实例没有心跳，崩溃后会一直留在列表里。调用方使用订阅来的健康实例，按权重选。进程退出要反注册。在 Kubernetes 里，就绪探针已经在做摘除，再注册一份 Nacos 就会有两套实例列表。

Kubernetes 自己的协调是 etcd，不经过 Nacos。业务要做选主、配置的有序变更、或者一把带租约的锁时，直接用 etcd 的数据面，不再套一层注册中心。

每次修改让集群的 revision 加一，这是一把只增的逻辑钟。Watch 从上次看见的 revision 接着收；这个 revision 已经被压缩，服务端会取消这次 Watch 并返回 compacted，客户端要先按当前 revision 做一次全量读，再从新的位置看。租约带 TTL，客户端大约按 TTL 的三分之一续约。进程停了，挂在这个租约上的键一起消失。

选主用 `concurrency.Session` 和 `Election`。候选人在同一个前缀下写键，创建 revision 最小的那个是主。`Election.Rev` 是这任领导的 fencing token。写下游时带上它，序号更小的写入拒绝。只等租约过期，旧主在过期前仍可能把更新写出去。Redis 那一节的 token 比对解决的是「别删掉别人的锁」；跨进程的资源还要这把只增的序号。

---

## 9. 分布式理论

一个进程里用锁和事务就能排好的事，拆到多台机器上之后要面对三件事：消息会丢、会重复、会延迟；机器会停；网络会把集群切成几块。下面这些名词都是在给这三件事选规则。

### CAP、PACELC、BASE

CAP 说的是发生网络分区的时候：分区的两侧不能同时做到「每次读都看到最新写入」和「两侧都继续对外服务」。选一致性，少数派就停止写入，甚至停止服务，这是 CP。选可用性，两侧都继续读写，等分区恢复再合并，读到的可能是旧的，这是 AP。没有分区的时候，延迟和一致性还可以同时往前做。把 CAP 理解成平时也只能三选二，会漏掉「分区才迫使你选」这个条件。

PACELC 把没有分区的情况补上：有分区（P）时在可用性（A）和一致性（C）之间选；没有分区时在延迟（L）和一致性（C）之间选。同步复制更一致，提交要等另一台确认，延迟更高。异步复制返回快，另一台还没收到。MySQL 半同步和 Redis 异步复制就是这个选择的两个点。

BASE 是另一端的工程说法：基本可用、软状态、最终一致。软状态是允许中间有一段时间读到旧值或中间步骤。最终一致是在没有新的写入、消息也都送达之后，副本会相同。它不是「可以一直不一致」。要有一个时刻用对账把两边对齐，并给这个延迟一个上限。

### 一致性怎么分级

强一致是读到的是上一次成功写入。主从都确认之后再返回，读也打到主，接近这个效果。线性一致还要求所有客户端看到的顺序和真实时间一致，通常要共识算法在多数派上提交。

顺序一致是所有客户端看到同一个操作顺序，这个顺序可以和真实时间略有偏差。因果一致保证有因果关系的操作不被看反，没有因果关系的可以不同顺序。读己之写是一个客户端至少能读到自己刚写的。会话一致把这个保证放在一次会话里。最终一致只保证收敛。

业务上按数据来选。余额和库存的扣减要强一致，用数据库条件更新或单主上的原子操作。时间线、搜索索引、统计可以最终一致，用消息把变更送过去。一个接口里混着两种数据时，把强一致的那部分单独读主库，其余走缓存。

### 多数派和副本

N 个副本，写入要 W 个确认，读取要 R 个确认。W + R > N 时，读集合和写集合一定有交集，能读到最近一次满足 W 的写入。常见是 N = 3，W = 2，R = 2，可以坏一台。W = N 是每写都要全部确认，一台慢就写不进去。W = 1 是最快的写，读必须接触全部副本才能确认自己看到的是最新的。

这是无主副本的算法。MySQL 和 Redis 主从是有主的：写进主，读主就看到这次写入；读从就要接受复制延迟。Kafka 的 `acks=all` 等的是当时的 ISR，不是超过半数。`min.insync.replicas` 设成 2、副本因子 3 时，确认只需要两份；ISR 缩到只剩 leader 时，这项保证也跟着缩掉。细节在消息队列的 Kafka 那一节。

法定人数也用来选主。三个节点可以坏一个，五个可以坏两个。两个节点没有多数派，网络一切开就无法决定谁是主，所以哨兵、etcd、ZooKeeper 都用奇数。

### Paxos

Paxos 要一组进程对一个值做出唯一的决定。进程会停，消息会丢、会重复、会延迟，发送方不会伪造消息。决定做出来之后，后面学到结果的进程都学到这同一个值。

三个角色可以叠在同一台机器上。提议者提出值，接受者投票，学习者得知结果。

每个提议有一个编号，所有编号能比大小，并且不重复。轮次放在高位、机器号放在低位，两台机器就不会拿出同一个号。

第一阶段是准备。提议者选一个比自己用过的都大的编号 n，向一个多数派发送准备请求。接受者如果还没有答应过更大的编号，就承诺不再接受编号比 n 小的提议，并把自己已经接受过的、编号最大的那条提议回给提议者。没有接受过任何提议，就只回承诺。

第二阶段是接受。提议者集齐一个多数派的承诺之后才能发接受请求。值按回包决定：有人已经接受过提议，就用这些回包里编号最大的那个值；谁都没接受过，才用提议者自己原来想提的值。请求是 (n, v)。接受者只要还没有答应过比 n 更大的编号，就接受它。

一个值被选定，是指一个多数派接受了同一个 (n, v)。学习者从接受者那里得知这件事。Basic Paxos 只决定一个值。一条日志是一串这样的决定，每个槽位跑一轮。

安全性来自多数派一定相交，再加上「有旧值就必须沿用」。假设编号 n 的值 v 已经被一个多数派接受。任何编号更大的提议者要进入第二阶段，都得再拿到一个多数派的承诺。这两个多数派至少有一个接受者是同一个人。这个人已经接受过 (n, v)，回包里会带上它；如果他后来又接受了更新的提议，那条更新的提议也是按同一规则从 v 沿用下来的。新提议者必须采用回包里编号最大的值，所以发出去的仍然是 v。对编号做归纳：凡是编号大于 n、并且真能完成的提议，值都是 v。两个不同的值不能同时被各自的多数派选定。

这套规则不保证某一次提议一定完成。两个提议者可以轮流用更大的编号打断对方，两边都过不了第二阶段。已经选定的值不会因此变成别的值，只是新的决定暂时做不出来。

工程上先固定一个稳定的提议者。它做一次准备，承诺覆盖后面的一段槽位；每条新命令只跑第二阶段，把一条日志复制到多数派。这是 Multi-Paxos。提议者宕了再重选，新提议者的准备阶段会把多数派上见过的旧值收集回来，按上面的规则补全。

Raft 把这套做法收成固定角色：强领导者、任期、日志必须连续，选举时就要求候选人的日志足够新。Paxos 允许槽位上暂时有空洞，实现者自己补。ZooKeeper 用的 Zab、etcd 用的 Raft，都是「稳定领导者加上多数派复制」。Chubby 用的是 Multi-Paxos。讲 Raft 的选举和日志匹配时，对应的就是这里的准备阶段和「旧值必须沿用」。

### Raft

Raft 是一种共识算法，etcd、Kubernetes 的一些组件、RabbitMQ 仲裁队列都用它或它的变体。一个任期里最多一个领导者。领导者把日志复制到跟随者，多数派写下之后，这条日志才算提交，然后应用到状态机。

选举：跟随者在选举超时内没收到心跳，就变成候选人，任期加一，向其他节点要票。每个节点在一个任期里只投一票，并且只投给日志至少和自己一样新的候选人。超时时间加随机抖动，避免所有人同时发起选举。少数派凑不齐多数票，选不出领导者。已经提交的日志只会由拿到多数派的那一个领导者继续复制。

日志匹配：同一索引、同一任期的日志在所有节点上是同一条。领导者只追加，冲突时用自己的日志覆盖跟随者。已经提交的日志在以后的领导者里仍然存在。这些规则保证提交过的操作不会因为选主被丢掉。

客户端只向领导者写。领导者还没复制到多数派就宕机时，客户端超时重试。操作本身要幂等，或者用一个客户端 id 和序号，让新领导者认出这是同一笔。Raft 保证的是日志的一致，不代替业务幂等。

### 时钟

物理时钟会漂移，NTP 校正时还可能往回跳。用本机时间做超时和证书判断可以，用两台机器的时间戳决定事件顺序不可以。Redis 锁的 TTL 用的是 Redis 服务器的时间，客户端时钟跳了不会直接让锁提前过期；主从切换仍会把还没复制的锁丢掉，那是复制的问题。

Lamport 时钟是一个整数，事件发生或收到消息时取最大值再加一。它可以给出一个全序，但看不出两件事是不是因果相关。向量时钟每个进程一维，能判断因果，维度随进程数增长。混合逻辑时钟把物理时间和逻辑计数放在一起，既接近真实时间，又在时钟回拨时靠逻辑位继续递增。订单号和日志的时间用单点生成的雪花 id，或者数据库的自增，不拿各台应用机器的 `time.Now` 拼顺序。

租约是带期限的所有权。持有者在期限前续约，到期没续，别人就可以拿走。Kubernetes 的选主、Kafka 的组成员、Redis 的锁都是租约。期限必须大于时钟误差和处理时间，续约失败要当成已经失去所有权，停下手上的独占操作。

### 失败的样子

宕机是进程停了，内存里的状态没了。恢复靠磁盘上的日志和启动时的重放。网络分区是两边都活着但不通。这时候最容易出现两个写入点。防护是少数派没有法定人数就不写。

脑裂是分区两侧各有一个主。Redis 哨兵要求多数派才故障转移，降低两侧同时选主的可能。旧主如果还在对外服务，客户端可能继续往旧主写。哨兵把旧主改成从、并要求从只读，是为了让它重新连上之后丢掉分叉的写入、向新主对齐。应用在切换窗口会写失败，按幂等重试到新主。

重复和乱序一起出现。消息至少一次，所以会重复。不同分区之间没有顺序。消费者按业务键去重，用状态机接受「取消先到、支付后到」这种乱序：状态只能沿允许的边走，走不通的消息先放着或进死信，不能把状态从已支付改回未创建。

幂等是这些失败模型的落点。同一笔请求执行多次，系统状态和执行一次相同。数据库唯一键、条件更新、幂等表、带 token 的锁释放，都是在实现这句话。

---

## 10. 系统设计

设计题先把约束问清楚，再估容量，再画主路径，最后把失败和一致性补上。组件从前面几节里选，不从名词表里堆。

### 怎么拆一道题

先问清楚这几件事：读多还是写多，峰值 QPS 和平均差多少，数据要留多久，能不能接受旧数据，有没有热点，用户在一个地域还是多个。问完才能决定要不要缓存、要不要分片、要不要异步。

然后做数量级估算。每秒请求数乘以单次读写的大小得到带宽。每天的新增行数乘以保留天数得到存储。峰值按平均的几倍到几十倍留余量，秒杀这种脉冲要单独算。连接数按副本数乘以每进程的池大小，对到数据库的 `max_connections` 和 Redis 的 `maxclients`。Go 是一个容器一个进程，不要再乘 worker。估算是为了选出瓶颈在 CPU、网络、磁盘还是锁，不是为了算到个位数。

接口和数据模型接着写。谁是聚合根，哪些查询要同时返回，哪些可以延迟。能用一张表和一个事务完成的，不拆成三个服务。

主路径画一条写、一条读。写路径标出事务边界和发件箱。读路径标出缓存层级和回源。然后单独写失败：下游超时怎么办，消息重复怎么办，缓存和库不一致怎么收口，扩容时数据怎么搬。观测放在最后：这条路径的延迟、错误率和积压看哪几个数。

延迟的预算按层扣。入口 3 秒的时候，里面每一跳都是几十到几百毫秒，数据库一次查询控制在几毫秒到几十毫秒。某一跳占掉大部分预算，就把它变成异步，或者加缓存。

### 几个反复出现的构件

id 在单库里用自增主键，插入落在索引的右边。要在应用里生成时用雪花 id：1 位符号，41 位毫秒时间，10 位机器，12 位序列，趋势递增，单毫秒单机能发 4096 个。机器号从库或 Redis 租，租约到期才能给别人。时钟回拨时拒绝发号或等到时间追上，不能把时间往回倒着发。UUID v7 也按时间排序，16 字节，做主键比 v4 友好。UUID v4 是随机的，会把 B+ 树写散。

限流算法按窗口的毛病来选。固定窗口在边界两侧各放进一整窗的量。滑动窗口把当前时间附近的计数加起来，边界平滑。令牌桶按速率补充令牌，允许短突发。漏桶按固定速率放行，出口更平。分布式计数用 Redis Lua，本地突发用 `rate.Limiter` 先挡一层。

一致性哈希用来把键分配到一批节点上，并且在节点增减时只搬一小段数据。把哈希空间收成一个环。每台物理节点用多个不同的种子算出很多位置，这些是虚拟节点。一个键哈希之后，沿环顺时针遇到的第一个虚拟节点就是它的归属，这个虚拟节点再映射回物理节点。

三台机器 A、B、C，每台在环上有很多点。键 k 顺时针先碰到 A 的某个虚拟节点，就由 A 负责。A 下线时，原先落在 A 那些点上的键，继续顺时针走到 B 或 C 的点，其他键不动。没有虚拟节点时，A 离开会把自己那一整段弧都交给下一家。虚拟节点把这段弧切成很多小段。每台一两百个就足够把倾斜压下去。机器权重不同时，权重大的放更多虚拟节点。

缓存节点用这套环，键是缓存 key。分库也可以用它：键是用户 id，节点是分片。和取模相比，取模在分片从 8 扩到 9 时几乎每行都要搬；扩到 16 这种两倍才只搬一半。一致性哈希在任意增减一台时只搬这一台名下的键。代价是实现要维护环，并且要处理节点抖动：一台缓存时好时坏，键会来回搬，打穿数据库。节点离开前先把流量摘干净，或者每个键沿环再找下一台做备份。

负载均衡把同一类请求送到同一台时用的是同一个环。请求没有本地状态时不必用它，轮询更均匀。

对账是最终一致的闭环。每隔一段时间用源表的聚合和目标对一下：订单数和库存扣减数、发件箱已发送和消费者处理数、缓存里的库存和数据库的库存。偏差超过阈值再告警，而不是每次有消息就人工看。

### 金额和时间

金额不用 `float64`。二进制浮点表示不了大多数十进制小数，`0.1` 加一千次对不上 `100`。库里的金额用整数的最小货币单位，例如分，列类型是 `BIGINT`。需要小数运算（税率、汇率）时用十进制类型，结果再按业务规则舍入到分。MySQL 的 `DECIMAL` 扫进 `float64` 会把精度丢掉，驱动侧扫成字符串或整数再解析。币种和数值放在一起，避免一个服务按元、另一个服务按分解读同一个字段。

时间用 `time.Time` 和 `time.Duration`，不要用裸的 `int` 表示「过多少毫秒」，字段名里也写上单位。`time.Now` 同时带墙上时钟和单调钟。`time.Since`、两边都有单调读数时的 `Sub`，不看墙上时钟，NTP 把时间拨回去也不会算出负的耗时。比较两个时刻用 `Equal`。`==` 还会比 Location 和单调读数，JSON 往返会丢掉单调读数，于是 `==` 失败、`Equal` 成功。日历上的「下一天」用 `AddDate`，固定的 24 小时用 `Add`。夏令时和月末都让「加 24 小时」和「加一天」不是同一件事。订单过期写清楚是哪一种。

### 秒杀

普通购买是查商品、建单、扣库存、付款、发货。秒杀是活动页、详情、立即抢、去付款。特点是价格低、瞬时并发高、限时限量、要预热。

读和写分开。详情走缓存或静态页。写的本质是并发扣减，要保证不超卖。秒杀接口单独路由、单独副本，限流放在网关上，超过的返回排队页。验证码和答题放在真正扣库存之前，把脚本流量挡在外面。

活动开始前把库存写进 Redis。扣减是一段 Lua，多条命令之间不会被别的客户端插进来。售罄标记如果只放在某个进程的内存里，别的进程看不到。标记的来源是 Redis：Lua 返回售罄，或者订阅一个售罄频道。Pub/Sub 不留存，进程刚启动时本地标记是空的，第一批请求打到 Redis，再把标记树起来。`sync.Once` 挡不住多个进程。

```lua
local left = tonumber(redis.call('GET', KEYS[1]) or '-1')
if left <= 0 then
  return -1
end
return redis.call('DECR', KEYS[1])
```

限购和扣减放进同一段 Lua，避免扣完又发现这个用户已经买过，还得把库存加回去：

1. 请求 id 已有结果，直接返回上次的结果。
2. `SET 用户限购键 1 NX EX 活动时长`。设不上就是已经买过。
3. 库存大于 0 才 `DECR`，否则删掉限购键并返回售罄。
4. 把用户、SKU、请求 id 放进队列。队列满了就 `INCR` 还库存、删限购键，接口返回稍后再试。

消费者按数据库能承受的速度建单。订单表的唯一键是用户 id + 活动 id + SKU，或请求 id。插入成功再写扣减流水。碰到唯一键就是重复投递，确认消息即可。数据库故障则不确认，稍后再消费。多次仍失败就把库存还回 Redis，并记入失败表。goroutine 的并发度对齐 `MaxOpenConns`，不要按消息条数往外扩。

定时任务用「预热库存 − 成功订单数」和 Redis 当前值比较，偏差超过阈值再告警。活动结束后以订单表为准做一次对账。

热点 SKU 的一个 key 会打在集群的一个槽上。库存拆成 `stock:{sku}:0` 到 `stock:{sku}:9`，请求随机选一段，这一段空了再试别的段。哈希标签 `{sku}` 让这些键落在同一个槽，一段 Lua 里可以连续尝试。拆段之后总库存是各段之和，展示用短缓存。

有人绕过接口直接改库时，缓存不会跟着变。订阅 binlog，而不是轮询表。`go-mysql` 扮成从库。MySQL 8 要 `binlog_format=ROW`、`binlog_row_image=FULL`、`binlog_row_metadata=FULL`，账号有复制权限，`server_id` 不和现有副本冲突。位点要自己存，断线从位点续上。也可以让独立进程拉 binlog 再推进 Kafka，应用只做消费者。解析到库存或价格变更后删缓存。这件事放在订阅进程里，不放在下单请求里。

### 搜索

搜索走 Elasticsearch，下单和库存走 MySQL。倒排索引按词指向文档 id。中文用 IK：`ik_max_word` 切得细，`ik_smart` 切得粗。7.x 起类型固定为 `_doc`。

商品索引：标题、关键词、副标题用 `text` 加 IK；品牌名、类目名、图片 URL 用 `keyword`；价格、销量、上架时间、是否有货用数值、日期、布尔。属性是一对多，映射成 `nested`（`attrId`、`attrName`、`attrValue`）。普通 object 数组会被拍扁，`颜色=黑` 和 `尺寸=L` 会错配成交叉组合。

客户端用 `go-elasticsearch`。进程退出时把还在飞的请求取消掉，连接跟着 `http.Client` 一起关闭。筛选放进 `bool.filter`：品牌、价格区间、有货、类目。`filter` 不参与打分，可以被缓存。关键词放进 `bool.must` 的 `multi_match`，字段用 `name^3`、`keywords^2`、`subTitle`。`should` 用来加分。用户选了价格或销量，就按那个字段排。分页用 `search_after`：排序字段里带一个唯一值，下一页把上一页最后一条的 `sort` 传回去。`from + size` 超过 `index.max_result_window`（默认 10000）会被拒绝。

品牌和属性筛选项是聚合。bucket 是分组，metric 是组上的统计。nested 属性要先进入 nested 文档再 terms。高亮用 `highlight`。

商品保存成功后发消息，消费者写 ES，失败可重试。搜索允许秒级延迟。429 和 503 退避重试，映射错误进失败队列。更新用 `if_seq_no` 和 `if_primary_term`：序号不一致就重新读再写。

默认 `refresh_interval` 是 1 秒，写完立刻搜可能还看不到。主分片数在索引建好后要改，就建新索引再切别名。查询可以打在副本上，写入打在主分片再同步。别名指到当前索引，对完数再原子拨过去。改分词器或已有字段的类型都要重建索引。

Master 管元数据和分片分配，Data 节点存分片并承担读写。应用连接超时设短，搜索读超时按慢查询上限设。聚合限制 `size` 和 `timeout`。客户端超时取消不了已经在节点上跑的查询。

### 容量不够时的顺序

先做单机能做的：索引、连接池、慢查询、缓存、无用的同步调用。然后加副本，应用无状态，数据库加读副本。写仍打在主库上，写成为瓶颈时再分库分表。查询带不上分片键、又经常要跨分片事务时，换成 TiDB，应用仍用 MySQL 协议。异步把不是主路径的写移出请求。每一步都要能说出现在的瓶颈数字，再选下一步。

---

## 11. 可观测性

可观测性是从系统外面发出的信号还原里面发生了什么。日志、指标、追踪是三种信号。只有日志时，要靠人去翻一次请求。只有平均值时，看不到慢的那百分之一。三者用同一个请求 id 串起来。

服务看四个信号：流量、延迟、错误、饱和度。延迟用直方图的分位，不用平均值。错误看 5xx 和业务错误码，不只看进程还在不在。饱和度看连接池等待、队列 lag、goroutine 数、CPU 节流。资源看使用率、饱和度和错误：磁盘 `%util` 和 await，内存 available，文件描述符。

RED 是给服务的：Rate、Errors、Duration。USE 是给资源的：Utilization、Saturation、Errors。两边一起看，才能分清是自己慢还是依赖慢。

### 日志

日志打到 stdout，一行一个 JSON。字段固定：时间、级别、服务名、请求 id、trace_id、span_id、路径、状态码、耗时、错误码。正文是消息，不把整份请求体打进去。手机号、令牌、密码不进日志。

```go
logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
slog.SetDefault(logger)
```

请求 id 放在 context 里，`InfoContext` 从上下文取。OpenTelemetry 的当前 span 里有 trace_id，和日志字段对齐，日志平台才能从一条错误跳到整条追踪。`Error` 带上 `err`，栈在日志系统里，不在 HTTP 响应里。`slog` 的默认文本格式不适合采集，生产用 JSONHandler。

访问日志每个请求一条，在中间件里打，包含状态码和耗时。业务日志只在状态变化和外部调用失败时打。循环里逐条打，流量一上来就会把磁盘和采集管道打满。级别在生产用 INFO，排查时对单个服务临时开 DEBUG，用完收回。

### 指标

指标是可聚合的数字。Prometheus 的客户端库里三种类型就够用：

- Counter 只增。请求数、错误数。看速率用 `rate()`，看增长用 `increase()`。
- Gauge 可升可降。正在处理的请求数、连接池占用、队列长度、goroutine 数。
- Histogram 观察值的分布。请求耗时。分位用 `histogram_quantile`，桶的边界按自己的 SLO 设，例如 50 毫秒、100 毫秒、300 毫秒、1 秒。

标签是有限集合：服务、路由模板、状态码、下游名字。用户 id、订单 id 放进标签，基数会把 Prometheus 打满。路由用模板 `/orders/:id`，不用真实路径。

`promhttp.Handler` 暴露在管理端口的 `/metrics`，由采集器拉取，不和业务端口一起对公网。Go 进程是一个，没有多 worker 各算各的问题。运行时自带的指标（堆、GC、goroutine、调度延迟）和业务指标放在一起看。

应用自己的指标补上框架没有的：Redis 命令超时次数、`DB.Stats` 的 `InUse` 和 `WaitDuration`、发件箱堆积行数、Lua 返回的售罄次数。HTTP 直方图只知道路由慢，不知道慢在下游还是慢在锁。

### 追踪

一条追踪是一次请求穿过所有服务的树。span 是树上的一个节点：一次 HTTP 调用、一次 SQL、一次 Redis。span 上有开始时间、结束时间、状态和几个属性。父 span 的 id 放在 `traceparent` 头里往下游传，W3C 的格式是标准的那一个。

Go 用 OpenTelemetry SDK。`TracerProvider` 加上 `BatchSpanProcessor`，导出到 OTLP 收集器。收集器再送到后端。导出放在进程外的收集器上，应用不直接对后端的地址负责。`otelhttp`、Gin 的 instrumentation、数据库和 Redis 的包装会读进来的头、给出去的调用注入头。进程退出时 `Shutdown`，把还没导出的 span 刷出去，宽限时间要留这一步。

```go
tp := sdktrace.NewTracerProvider(
    sdktrace.WithBatcher(otlpExporter),
)
otel.SetTracerProvider(tp)
otel.SetTextMapPropagator(propagation.TraceContext{})
```

采样在流量大时按比例，错误和慢请求要能留下。头部采样在请求进来时就决定，实现简单，可能把一次慢请求丢了。尾部采样等整条追踪结束再决定，能保住错误，代价是收集器要先把追踪攒住。入口先用按比例加「错误一定采样」，就够定位大部分问题。

span 上只放定位需要的属性：订单 id、下游地址、重试次数。SQL 可以记语句模板，不记绑定进去的隐私参数。1.25 的 flight recorder 补的是进程内的调度和 GC，和这条跨服务的追踪是两份材料，慢请求先看追踪，怀疑运行时再倒 recorder。

### SLO 和告警

SLI 是测量出来的比例，例如 30 天里耗时小于 300 毫秒且不是 5xx 的请求占比。SLO 是这个比例的目标，例如 99.9%。错误预算是 1 减去 SLO，预算烧完就停止普通发布，先处理可靠性。

告警打在症状上：错误率、延迟分位、预算燃烧速度、队列 lag、探针失败。CPU 高本身不叫醒人，它引起的延迟才叫醒人。燃烧速度用两个窗口一起看，短窗口抓突发，长窗口抓慢性消耗，避免一次抖动就响。

每个告警带一个下一步：看哪张仪表盘，先查哪个下游。没有下一步的告警会在几次之后被关掉。就绪失败和存活重启分开告，前者是依赖，后者是进程自己。`OOMKilled` 和「GC 把 CPU 打满但进程还在」分开告，前者加内存或查泄漏，后者先看分配速率和 `GOMEMLIMIT`。

### 接口变慢怎么查

先分清范围。看入口的流量、延迟分位和错误率：是全站一起变慢，还是一条路由、一个用户。时间和最近一次发布对齐。全站的 5xx 先看网关到 Pod 的连接失败和就绪探针，再看应用。

然后看饱和，而不是先重启。正在处理的请求数、goroutine 数、连接池里正在用的连接、池等待时间。池等待升高，请求堆在应用里，原因在池子后面的数据库、Redis 或下游。goroutine 数升高、CPU 不高，多半是都堵在 IO 上。CPU 升高且栈集中在 `runtime.gc` 和标记辅助，是分配太快或 `GOMEMLIMIT` 太紧。

用这一条慢请求的 trace 看时间花在哪个 span。SQL 长，就去数据库上看锁和扫描行数：`sys.innodb_lock_waits`、慢查询、`EXPLAIN` 里的 `rows`。行锁等待和死锁对应 MySQL 那一节的 1205 和 1213。Redis 长，看 `slowlog`、是不是在 `bgsave`、是不是一个大 key。下游 HTTP 长，看对方的错误率和超时，本进程的熔断有没有打开。

机器放在最后。`container_cpu_cfs_throttled_seconds_total` 在涨，同时延迟升高，是 CPU limit 在节流。1.25 之前先看 `GOMAXPROCS` 是不是还等于节点核数。退出码 137 且原因是 `OOMKilled`，是内存上限。CPU 高但延迟和错误率都正常时，先记下，不把它当成这次故障的原因。

依赖都正常、只有新版本变慢，就回滚镜像，再对这条路由做剖析。回滚解决不了数据库里已经变差的执行计划，那种要单独处理 SQL。

---

## 12. 云平台

云把机器、网络、负载均衡和托管数据服务按 API 提供出来。区域是地理位置上的一组数据中心，可用区是区域内电力和网络独立的一块。同可用区延迟低，一台可用区的故障不会直接拆掉另一台。跨区域延迟高一个数量级，用来做容灾，不用来做同步调用。

应用无状态，可以放在任一可用区。状态放在托管的数据库、缓存和对象存储里，由这些服务自己做多可用区复制。Go 进程里不保存要跨请求保留的数据，上传的文件进对象存储，会话进 Redis 或做成签名令牌。

### 网络和入口

VPC 是一块隔离的私有网络。子网按可用区放，应用子网和数据子网分开。安全组是有状态的防火墙，最小权限打开：入口只接受负载均衡的流量，数据库只接受应用安全组，管理端口不对公网开放。出网走 NAT，实例不必带公网地址。

负载均衡放在至少两个可用区。健康检查打就绪接口，失败阈值和 Kubernetes 的探针错开一点，避免同一瞬间两边一起摘除。TLS 在负载均衡上终止。静态资源和上传文件走对象存储加 CDN，应用只签发上传地址或回源，不在 Pod 的磁盘上存用户文件。

域名解析到负载均衡。内部服务用云的内网 DNS 或 Kubernetes 的 Service，不把内部地址写死成 IP。IP 在扩缩容和故障转移时会变。

### 托管数据服务

关系库用多可用区的主备。应用写主、读可以走只读实例，读己之写的查询留在主上。备份自动做，恢复时间要试过。参数和前面 MySQL 那一节一致：`utf8mb4`、`ROW`、提交时刷 redo 和 binlog。连接数按所有应用副本的 `MaxOpenConns` 加起来设，托管实例的最大连接数是选规格时的一个上限。

缓存用托管 Redis，持久化和淘汰策略按用途设。主从切换会有短暂停写。客户端用哨兵或集群协议重新发现主，地址不写死。内存打满会触发淘汰或拒绝写，监控 `maxmemory` 的使用率。go-redis 的 `MaxRetries` 在切换窗口里可能把一次写重试出去，非幂等命令要关掉重试。

对象存储按桶划分权限。应用用角色拿到临时凭证，不把长期密钥放进镜像或环境变量文件。私有桶通过短时签名 URL 给浏览器上传或下载。桶可以跨区域复制，用来做备份，复制有延迟。

消息队列如果用云上的托管 Kafka 或 RocketMQ，投递语义和自建相同。换的是磁盘、副本和扩分区由平台做。积压、幂等和死信仍是应用的责任。

### 计算和发布

容器服务跑在 Kubernetes 上。节点组分布在多个可用区，Pod 的拓扑约束让同一 Deployment 的副本不要全挤在一个可用区。`topologySpreadConstraints` 按可用区打散。一个可用区不可用时，另一个区的副本还在，配合 PDB 和至少两个副本。

发布用不可变镜像。构建在流水线里完成，集群只拉取。回滚是把 Deployment 的镜像标签指回上一个提交。数据库迁移作为发布前的 Job，并且向后兼容：先加列和双写，再切读，再停旧列。先做不兼容的 DDL 再滚动应用，滚动的中途旧代码会写失败。

配置和密钥放在平台的密钥管理或 Kubernetes Secret 里，由卷挂进容器。环境变量适合非机密的开关。密钥进了环境变量就会出现在崩溃转储和 `/proc` 里，挂成文件、只给进程读，范围更小。

自动扩缩看饱和度，不看时刻。定时扩容用在知道脉冲何时到来的场景，例如活动开始前把秒杀的副本拉起来，HPA 在脉冲已经打进来之后才反应。缩容设稳定窗口，避免 RSS 还没下来就再扩一轮。

成本按三个数看：副本是不是远高于实际 QPS，日志和追踪是不是全量采集，跨可用区流量是不是因为把数据库和所有应用放在了不同的区。同可用区优先放同一组会互相调用的服务。规格从能撑住连接数和内存的最小档开始。`GOMEMLIMIT` 和 CPU limit 按这个规格设，不按节点的整机核数设。

### 权限和故障

人和服务都用角色，不用共享的长期密钥。流水线发布用的角色只能推指定仓库和滚动指定命名空间。应用的角色只能访问自己的桶和自己的库账号。人的排障角色默认可读，写生产要单独提升，并留下审计。

可用区故障时，负载均衡把流量送到剩下的区，数据库切到备用区。应用要能在写失败时重试幂等请求。连接被重置后，下一次使用会碰到 `driver.ErrBadConn`，池会丢掉这条连接再换。`ConnMaxLifetime` 让旧主上的连接在切换后不会留太久。`rejectReadOnly=true` 避免继续往已经变成只读的旧主上写。

同城两个可用区之间可以做同步复制：MySQL 半同步、托管库的多可用区主备，延迟还在毫秒级。两地三中心的第三个副本放在另一座城市，只做异步复制，RPO 是秒到分钟。跨地域的确认不放进下单事务。切到异地是运行手册里的决定，事先演练过；应用靠幂等重试接住切换窗口，靠读主库接住自己的写入。

多租户如果跑在同一个集群里，用命名空间、NetworkPolicy 和资源配额分开。一个租户的请求打满节点，会让同节点的其他 Pod 被节流或驱逐。重要服务用独立的节点组。Go 的 CPU 节流会先表现为 GC 和尾延迟变长，再表现为接口超时。

---

## 13. 安全底线

鉴权放在网关，授权放在资源上。网关证明「你是谁」。订单服务证明「这行是你的」。两件事都做，只做前面那件，换一个订单 id 就能读到别人的数据。

### 查询和越权

`database/sql` 的 `?` 是占位符，驱动负责绑定。GORM 的 `Where("id = ? AND user_id = ?", orderID, userID)` 同样走绑定。`fmt.Sprintf` 把路径参数拼进 `Raw`，解析器会把用户的引号当成语法。`IN` 列表用占位符展开，不把一串 id 连成一段 SQL。`ORDER BY` 的列名只能来自白名单，不能从查询参数直接拼进去。

ORM 挡不住越权。按主键 `First` 只要行在就返回。查询条件里带上当前用户，用户 id 来自网关写入的头，或者来自已经验过的主体，不来自请求体里的用户字段：

```go
var order Order
err := db.WithContext(ctx).
    Where("id = ? AND user_id = ?", orderID, userID).
    Take(&order).Error
if errors.Is(err, gorm.ErrRecordNotFound) {
    http.Error(w, "not found", http.StatusNotFound)
    return
}
```

不存在和不属于当前用户都返回 404，避免用 403 告诉对方这个 id 是有主的。管理接口走单独的角色和路由。列表同样按用户过滤。

改状态的接口带上登录态里的用户，再校验这条资源当前允许这个动作。只校验「登录过」等于水平越权。

### 密码和令牌

密码只存单向哈希。用 argon2id 或 bcrypt，把一次校验调到几十毫秒，成本参数随机器更新。SHA-256、MD5 可以穷举，不能当密码哈希。每个密码自己的盐由算法生成，存在哈希结果里。校验用库提供的函数，它按固定时间比较。

登录失败和账号不存在返回同一句话。日志里不写密码、验证码和完整令牌。令牌和分布式锁的 token 用 `crypto/rand` 生成，`math/rand` 的序列可以预测。令牌的签发和注销按网关那一节。内部服务之间用单独的身份，不把用户的访问令牌转发到和这次请求无关的下游。

### 上传和向外的请求

上传限制大小，先看 `Content-Length`，再用 `http.MaxBytesReader` 限制实际读到的字节数。只改头就传进一个大文件时，读到上限会失败。文件名由服务端生成，用户提供的文件名只做下载时的展示，并且去掉路径和换行。类型用文件内容判断。对象放在私有桶，下载用短时签名 URL。业务 Pod 的磁盘不留用户文件。

服务如果要按用户给的地址去拉取，就有 SSRF。地址先解析成 IP，再判断是不是回环、链路本地、私网和云元数据地址 `169.254.169.254`。不在允许的域名里就拒绝。重定向要逐跳重新检查。`http.Client` 的 `CheckRedirect` 里做这件事，超时和重定向次数都设上。能用固定的上游就不要接收任意 URL。

pprof、`/metrics` 和调试路由只绑在内网管理端口，和业务端口的认证分开。Gin 的 Release 模式关掉调试输出。错误响应不带栈和 SQL。`html/template` 会转义，拼 HTML 时不用 `text/template` 直接把用户内容嵌进去。密钥用角色的临时凭证，不进镜像。依赖在流水线里跑 `govulncheck`。

---

## 开发流程

前面各节讲一条请求怎么走完。这一节讲这份代码怎么从几个人的笔记本变成线上还能退回去的版本。例子是图书馆：登录、查书、借书、还书，书目和借阅记录在 MySQL。缓存、会话、主从、队列、网关和编排的机制已经写在对应的节里。这里定它们出现的顺序，以及哪一步会把别人的工作盖掉、哪一步会让全校一起借不了书。

### 版本控制

几个人同时改登录、查书、借书、还书。把文件发到群里，后传到的那份会盖掉先传到的，也看不出借期是谁从 14 改成了 30。Git 管的是整个工作树。一次提交是这一刻全部文件的快照，再加上指向父提交的指针。分支是一个可以移动的名字，指着某一次提交。`git diff` 看工作区和当前提交差了哪几行。`git log -p`，或者两次提交之间的 diff，看历史里具体改了什么。把分支指回从前某一次提交，工作树回到那时的快照。

远程仓库是大家共用的那一份。改完在本地提交，再推上去，别人从远程拉。三个人用的是同一条历史。

`main` 上只放能编译、测试能过、可以拿去部署的快照。借书和还书各自从 `main` 拉出分支。功能要写几天，当天只写了一半，就留在自己的分支上。半成品推进 `main`，别人一拉，整个程序跑不起来。Git 把每条分支上的提交按父指针串起来。两条分支从同一次提交分出去，之后各自追加。

写完用 merge 收回 `main`。Git 从当初分叉的那次提交起，把两边末端的快照合成一次新的合并提交。两边改的是不同文件，自动合成。两边改了同一处，例如借期一边写成 14 天、一边写成 30 天，合并停住，冲突标在文件里。人改完、重新提交，这次合并才算完成。

rebase 把当前分支上分叉之后的提交摘下来，接到目标分支现在的末端后面重放。历史看起来是一条直线，分叉点不再出现在图上。个人分支还没被别人拿去接着做时，用 rebase 跟上 `main`，冲突先在自己这边解决。已经推送到公共分支、队友拉过的提交不再 rebase。重放会改掉提交编号，队友本地的历史对不上，下一步就是强制推送。`main` 上已经存在的提交同样不再变基。收进 `main` 用 merge，冲突留在这一次合并里。仓库规则写明这一点。有人用 rebase 收、有人用 merge 收，历史会变成两种形状。

### 评审和持续集成

`main` 设成保护分支，不能直接推。借书的判断写反了，直接推进去，下一次发布全校都借不了。改动从自己的分支提起 Pull Request。自建的 GitLab 上这一步叫 Merge Request。至少一名队友看过才能合并。人看的是库存不足、重复提交、还书时这本书根本没借出，这些边界会不会漏。

每次推送和每次提起评审，流水线跑同一套检查，没过就不能合并。`go test ./...`，会碰共享状态的包再加 `-race`，`govulncheck` 看已经编进二进制的漏洞。模糊测试、基准和 PGO 的用法在测试和运行那一节，流水线里至少留下测试、竞态和漏洞这三道。镜像用 `CGO_ENABLED=0` 的静态二进制，标签用这次提交的 git 编号，不把 `latest` 当唯一标识。

预发和生产用同一份镜像，只换配置和连接的库。在预发重新编译，两边就不是同一次提交，评审看过的代码和生产跑的代码会岔开。预发用测试数据，或只放隔离的流量。在预发把借书、还书、库存不足和重复提交走一遍，再允许进入生产。

### 从一台机器到多份实例

笔记本上的地址，别的同学连不到。先放到一台常年开着的云服务器上。校园内网里专门对外响应的那台机器，是同一种角色。一台的 CPU 满了，或者这台宕了整个馆都停，就再跑几份相同的程序。一台物理机上可以跑多份，各自有自己的 CPU 和内存限额。口头说的加机器，和在同一台上加实例，都是水平扩展：用更多份相同的程序分担请求。

前面加一层反向代理。对外一个地址，按算法把请求分到后面的实例，实例健康检查失败就从名单里摘掉，拿不到正常回复就停止把新请求打过去。它把后端实例的真实地址藏在后面。正向代理站在客户端这一侧，替客户端去访问外面。反向代理站在服务这一侧，替服务接收外面进来的请求。四层和七层怎么转发，在负载均衡那一节。

实例一多，登录态不能放在某一台的内存里。学生在第一台登录，下一跳被分到第二台，第二台的内存里没有这个会话，学生就被当成没登录。会话编号和学号放进 Redis，每台都按同一个编号去取。Cookie、过期和注销在登录态那一节。

查书远多于借书和还书。读可以打从库，写只打主库。主库把日志送到从库需要时间。学生刚还完最后一本，立刻查从库，仍可能看到未还。这种读打主库。热门教材的书目放进 Redis，没命中再查 MySQL，并设过期时间。不存在的书号、同一批 key 同时过期、更新之后删缓存，按缓存那一节做。借书和还书改的是库存和借阅记录，走主库事务。Redis 里的数字只是加速用的副本。对不上时以 MySQL 为准，由对账把缓存盖回去。

全网每一份从库和每一份缓存都要立刻看到这次还书，就要等复制和缓存失效都完成再返回，延迟会抬上去。允许从库和缓存有短暂延迟，查书才能留在低时延这一侧。借还这种要强一致的写，走主库事务。这个选择就是 CAP 和 PACELC 在这条业务上的落点，名词在分布式理论那一节。

### 同一份镜像上线

评审和检查都过了，还不能在生产机器上手工拷文件。各台机器的运行时、依赖和配置会漂：一台的语言版本和另一台不一样，同一份代码在这边能跑、在那边启动失败。镜像把程序、依赖和运行时打在一起。容器共享宿主机的内核，不自带一整套操作系统，所以比虚拟机轻，启动是秒级。虚拟机是在一台物理机上用虚拟化切出的一整台机器，自己有内核。静态二进制怎么进 distroless，在 Docker 那一节。

持续交付是：`main` 上每一份通过检查的提交都能打成镜像，放到预发，差一次人工确认就能进生产。持续部署是确认这一步也交给流水线。借还会改库存，生产由人点。点下去之后，十几个实例同时换成新镜像，一次错误判断会让全校一起失败。先让负载均衡把一小部分请求切过去，看借书失败率和延迟。稳了再一批一批放大。不对就把流量指回上一份镜像。回滚改的是流量指向的镜像标签。线上机器上不改判断条件。滚动更新先起一个新副本、就绪后再停一个旧的，`maxUnavailable: 0`、`maxSurge: 1`，在探针、资源和发布那一节。灰度决定切多少流量，滚动决定怎么换副本。新副本先接一小部分，确认后再按滚动把其余副本换掉。

### 什么时候拆服务

一个进程里，还书的错误发布会把查书一起换掉，借书忙的时候电子书也得跟着扩容。一个有问题的依赖把进程打满，登录和借书一起停。边界清楚、发布和扩容的节奏已经不同，再拆成各自拥有数据的服务。用户、账号和登录会话收成所有学校共用的一块。每个团队发自己的版本。

对外给浏览器和手机的接口继续用 HTTP 和 JSON。对内字段稳定、调用密的，用 IDL 和 gRPC：先加字段，旧客户端按字段号跳过不认识的字段。连接、序列化和超时由框架处理。契约、超时、重试、熔断和舱壁在微服务那一节。拆开之后一次借书要跨网络。网络会丢、会慢、会重复，这些保护跟着拆分一起做。

### 编排、入口和故障

几十个服务、每个多份实例之后，人已经算不清新容器该放哪台还有空闲的机器上。Kubernetes 要的是期望状态：借阅服务要 50 个副本。控制面把容器补到这个数。半夜有容器停了，或者某台机器坏了，它把副本再拉回来。开学前把副本数调高，闲下来再调低。声明式的对象、探针和滚动更新在 Docker 与 Kubernetes 那一节。

实例的地址会变。借阅服务把用户服务的 IP 写进配置文件，扩容或重启之后这份配置立刻过期。服务发现给出一份还能连的地址：实例带着心跳或健康检查登记，调用方查这份名单，并在本地缓存一小段时间。Kubernetes 里这份工作由 Service 和 CoreDNS 做。虚拟机或混部再用 etcd、Nacos 或 Consul。注册中心和 Redis 一样，是业务旁边的基础设施。注册中心自己停了，发现就停。选一套。Service 和 Nacos 各维护一份名单时，调用方会拿到两套地址。

调用链上的保护按这个顺序看：先从名单里挑一个实例，连接放进连接池，请求设超时，只有可以重复执行的操作才重试，失败比例高了就熔断，慢调用放进单独的并发额度里。推荐服务卡死时，主页面先不展示推荐，借书仍然能做完。熔断过一段时间进入半开，放少量请求试探，成功再合上，失败继续断开。这些细节在同步调用上的保护那一节。

外面进来的不再只是浏览器。后端已经是几十个服务。调用方记不住每个服务的内部地址，脚本也不能直接打到借阅服务上刷座位。网关是外部请求进集群的统一入口：按路径把借书转到借阅服务，把查书转到书目服务；TLS 在这里结束；登录态在这里验一次，再把用户 id 传给后面。限流也放在这里，把多余的流量挡在业务进程之外。网关自己要有多份，单份网关停了，整个入口就停。进来之后，服务之间的调用走集群内部的发现，不再绕回网关。路由、鉴权和限流在网关、配置与发现那一节。

### 异步、观测，以及网格要不要上

借书在用户服务确认没欠费、库存也扣完之后，这件事本身已经做完。通知、统计、推荐可以晚几秒。把它们留在同一次请求里，通知服务一停，借书接口就报错。请求一多，当场拉起的任务会把下游打满。进程重启，还在内存里的任务就没了。以后再加积分，借阅服务还得再改一次。

消息队列放在借阅服务外面。借阅服务往队列里写一条借书成功的事件，写成功就可以返回。通知、统计各自订阅。队列把一瞬间的高峰先接住，消费端按自己的速度处理。Kafka 和 RocketMQ 把这条消息复制到多台机器上。坏掉一台，已经确认的消息还在。投递是至少一次，同一条提醒可能再来，消费端按借阅记录的主键做成幂等。可靠、削峰和这条解耦在消息队列那一节。副本怎么确认，在 Kafka 那一节。

组件多了之后，学生借书偶尔失败，已经不能在一台机器上打断点。三份信号一起留。指标看流量、耗时和错误率，灰度时盯的就是借书失败率。日志记扣库存失败、推送异常，并且集中收集，不留在容器被删掉就消失的本地盘上。追踪给一次请求一个编号，从网关穿过借阅、数据库，进消息队列再出来，都带着它。拿这个编号才能看出死在哪一环。三份信号在可观测性那一节。

服务网格是再往后的一步。Java、Go、Python 混在一起，超时、重试、熔断和链路加密若都写在各自的框架里，改一个超时就要每种语言发一版。网格在每个业务容器旁边放一个代理，进出的流量先经过它。这些代理是数据面。另一套控制面不碰具体请求，把超时和限流的规则推给全部代理。接口生成和语言内部的序列化仍然留在业务进程里。每一次调用多一跳，延迟、CPU 和内存都会上去。几十个服务、语言还没混到框架管不过来时，框架里的保护就够。网格换来的是治理逻辑可以单独发版。边车的重试和应用自己的重试叠在一起会相乘，这一点在同步调用上的保护那一节。

| 阶段 | 图书馆里先碰到的事 | 往下看 |
| --- | --- | --- |
| 几个人改同一份代码 | 借书、还书分分支，`main` 只留能运行的快照 | 本节的版本控制和评审 |
| 一台机器顶不住 | 多实例、反向代理，会话放进 Redis | 负载均衡、登录态 |
| 数据库顶不住 | 读可以从库，热门书目进缓存，借还走主库 | MySQL 复制、缓存 |
| 上线一次换光 | 同一份镜像，先切一小部分流量 | Docker，探针、资源和发布 |
| 一个程序互相拖累 | 按业务拆开，各自发布和扩容 | 微服务 |
| 容器该放哪台机器 | 声明副本数，地址用服务发现 | Kubernetes，网关、配置与发现 |
| 借书成功后还要通知 | 事件进队列，消费端按主键幂等 | 消息队列 |
| 偶尔失败查不到死在哪 | 同一个请求编号穿过日志、指标和追踪 | 可观测性 |

这一节用图书馆把顺序串起来。前面各节的例子仍是订单。两条业务用的是同一套规则：保护分支、同一份镜像、写走主库、允许读有短暂延迟、跨服务的通知走队列。

---

## 依据

- [InnoDB 锁](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking.html)，[MVCC](https://dev.mysql.com/doc/refman/8.4/en/innodb-multi-versioning.html)，[半同步](https://dev.mysql.com/doc/refman/8.4/en/replication-semisync-interface.html)
- [database/sql 连接池](https://go.dev/doc/database/manage-connections)，[GORM 事务](https://gorm.io/docs/transactions.html)，[sqlc](https://docs.sqlc.dev/)，[TiDB 架构](https://docs.pingcap.com/tidb/stable/tidb-architecture/)，[TiDB 隔离级别](https://docs.pingcap.com/tidb/stable/transaction-isolation-levels/)，[TiDB 悲观事务](https://docs.pingcap.com/tidb/stable/pessimistic-transaction/)
- [Go 1.27 发布说明](https://go.dev/doc/go1.27)，[泛型方法](https://go.dev/blog/generic-methods)，[泛型的字典和 GC shape](https://go.googlesource.com/proposal/+/master/design/generics-implementation-dictionaries-go1.18.md)，[Go 1.26 发布说明](https://go.dev/doc/go1.26)（Green Tea GC 默认开启，goroutine leak profile 当时还是实验），[Go 1.25 发布说明](https://go.dev/doc/go1.25)，[容器里的 GOMAXPROCS](https://go.dev/blog/container-aware-gomaxprocs)
- [Go 1.24 发布说明](https://go.dev/doc/go1.24)，[Swiss Table map](https://go.dev/blog/swisstable)，[Go 内存模型](https://go.dev/ref/mem)，[GC 指南](https://go.dev/doc/gc-guide)，[分配器](https://go.dev/src/runtime/malloc.go)（8KB 页、32KB 以下走 size class、tiny 块 16 字节），[PGO](https://go.dev/doc/pgo)，[单调钟](https://pkg.go.dev/time#hdr-Monotonic_Clocks)
- [net/http](https://pkg.go.dev/net/http)，[Gin](https://gin-gonic.com/docs/)
- [Redis 持久化](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)，[集群规格](https://redis.io/docs/latest/operate/oss_and_stack/reference/cluster-spec/)，[分布式锁](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/)，[HEXPIRE](https://redis.io/docs/latest/commands/hexpire/)，[BF.ADD](https://redis.io/docs/latest/commands/bf.add/)，[go-redis](https://github.com/redis/go-redis)，[锁过期后的 fencing token](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html)
- [gRPC 核心概念](https://grpc.io/docs/what-is-grpc/core-concepts/)，[gRPC Go](https://grpc.io/docs/languages/go/)，[JWT](https://datatracker.ietf.org/doc/html/rfc7519)
- [RabbitMQ 仲裁队列](https://www.rabbitmq.com/docs/quorum-queues)，[Kafka 复制与高水位](https://kafka.apache.org/documentation/#replication)，[Kafka 投递语义](https://kafka.apache.org/documentation/#semantics)，[合格主副本 ELR](https://kafka.apache.org/41/operations/eligible-leader-replicas/)
- [Git 分支](https://git-scm.com/book/en/v2/Git-Branching-Basic-Branching-and-Merging)，[变基](https://git-scm.com/book/en/v2/Git-Branching-Rebasing)
- [RocketMQ 客户端](https://github.com/apache/rocketmq-clients)，[JetStream 保留策略](https://docs.nats.io/learn/jetstream/retention-policies)，[JetStream 应答](https://docs.nats.io/learn/jetstream/acknowledgment)，[nats.go](https://github.com/nats-io/nats.go)
- [Kubernetes Pod 生命周期](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/)，[持久卷](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)，[client-go DeltaFIFO](https://github.com/kubernetes/client-go/blob/master/tools/cache/delta_fifo.go)，[client-go 选主示例](https://github.com/kubernetes/client-go/blob/master/examples/leader-election/main.go)
- [Nacos Go SDK](https://github.com/nacos-group/nacos-sdk-go)，[etcd 的 revision、Watch 和租约](https://etcd.io/docs/v3.7/learning/api/)，[groupcache](https://github.com/golang/groupcache)
- [Paxos Made Simple](https://lamport.azurewebsites.net/pubs/paxos-simple.pdf)，[Raft](https://raft.github.io/)
- [OpenTelemetry Go](https://opentelemetry.io/docs/languages/go/)，[Uber Go 风格指南里的时间](https://github.com/uber-go/guide/blob/master/style.md)


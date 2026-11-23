# 传统后端学习笔记（Java）

一份 Java 服务从请求进入到数据落地的知识体系。业务数据以 MySQL 为准，Redis 承担缓存、计数和原子扣减，消息队列承担异步和削峰，网关承担入口的鉴权和限流，容器和 Kubernetes 负责交付。进程用 Spring Boot 3.4 / 3.5 接请求，用 MyBatis 访问数据库，连接池用 HikariCP。运行时的基线是 Java 21；虚拟线程在 `synchronized` 上的行为单独注明 JDK 24 以后的变化。

组件按仍在维护的线来写：MySQL 8.4、Redis 7、Spring Boot 3.5、MyBatis 3、HikariCP、Lettuce、Redisson、Spring Cloud Gateway、Nacos 2、Sentinel、Seata、RocketMQ（4.x Remoting 与 5.x gRPC 两套模型）、Kafka 3、Micrometer Tracing、OpenTelemetry Java。

一条写请求穿过这些层：

1. 连接在 Linux 上完成 TCP 握手。TLS 在 Ingress 结束，Tomcat 的 NIO 连接器收到 HTTP/1.1，请求交给平台线程或虚拟线程。
2. 网关校验令牌、写入请求 id、按用户限流，再转到 Service。
3. `DispatcherServlet` 匹配路由。过滤器把请求 id 放进 MDC，Bean Validation 校验参数。
4. 库存、限购这类读改写在 Redis 里用 Lua 一次做完。
5. 订单行和发件箱行在同一个 Spring 事务里提交。提交同时落 redo 和 binlog。
6. 事务提交之后，后台循环把发件箱推到队列。消费者建下游单据或更新搜索索引，按业务键幂等。
7. 日志、指标、trace 带同一个请求 id。副本挂了由 kubelet 拉起。堆内存耗尽和 cgroup 内存耗尽是两种退出。

读请求先看进程内的 Caffeine，再看 Redis，最后查库并回填。强一致的读打主库。

下面先走完一次提交和一次读详情。随后十三节对应这条路径上要能讲清的机制。MySQL 和 Redis 分开写，两边的故障模型不一样。网络一节里有 gRPC 和 WebSocket/SSE，Kubernetes 一节里有持久卷，分布式理论一节里有 Paxos，最后一节是安全底线。

---

## 一次请求

用两笔请求把浏览器、网关、应用和数据库走完。写请求是用户在页面上提交一笔普通订单。读请求是打开这笔订单的详情。业务数字以 MySQL 为准，Redis 做扣减前的原子判断和详情缓存。这一节定顺序、每一层的责任，以及提交在中途断开时浏览器、Redis、MySQL 各剩什么。锁、复制、连接池和发件箱的机制在后面各节。

### 三层各管一段

| 层 | 跑在哪 | 这次请求里做的事 |
| --- | --- | --- |
| 前端 | 浏览器里的 React / TypeScript | 收集表单，带登录态发出 HTTP，按错误码改界面 |
| 后端 | 网关进程和 Spring Boot 进程 | 鉴权、限流、生成请求 id，按顺序调用 Redis 和 MySQL |
| 数据库 | MySQL、Redis | MySQL 留下订单、库存和发件箱；Redis 留下可重建的计数和缓存 |

一次点击在浏览器里开始，在数据库里结束。中间每一跳带着同一个请求 id，日志才能对上。浏览器只发 HTTP。MySQL 和 Redis 的连接留在后端进程里。

### 写：提交订单

页面上已有商品、数量和收货地址。页面上的库存只是展示，提交时再问一次后端。

```text
浏览器
  → 网关：验令牌、限流、盖上用户 id、传请求 id
  → DispatcherServlet：校验这张单属于这个用户
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

请求先到网关。网关校验令牌，从令牌取出用户 id，写进往下传的身份。请求体里客户端自己填的用户 id 被盖掉。限流按用户；没有登录态的接口按 IP。请求体超过上限直接拒绝。通过之后把请求 id 往下传。头里没有，过滤器自己生成，放进 MDC。日志、Redis 和数据库操作都带上它。交给线程池的任务要用 `TaskDecorator` 把 MDC 拷过去，执行完再清掉。

控制器处理这张单本身：Bean Validation 看参数是否齐全、数量是否为正，服务再看地址是否属于这个用户。网关不知道订单属主。

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

返回 `-1` 就报库存不足，MySQL 不打开事务。返回 `held` 才进 Spring 事务。同一事务里写订单行、库存的条件更新、发件箱和幂等行。库存更新写成：

```sql
UPDATE inventory
SET stock = stock - #{n}
WHERE id = #{id} AND stock >= #{n}
```

Mapper 返回的行数为 0，事务回滚。加回用另一段 Lua：键仍是 `held` 才 `INCRBY`，并删掉这把键，避免两次加回。加回使用 Lettuce 自己的命令超时，不沿用这次请求已经耗尽的截止时间。加回失败，或进程在加回之前停了，Redis 就少一截；键过期之后的重试会在这个偏少的值上再减。对账用 MySQL 里的库存覆盖 Redis，不在偏少的值上累加。不确定提交成功没有，先查主库幂等行，不先加回。这些行一起提交，或一起没有。

事务提交之后，接口返回「已下单」。搜索索引、通知、下游单据留在事务外面。另一个循环扫发件箱里未发送的行，推到队列，成功再标成已发送。进程在推送前停了，重启会再推，消费者按订单号做幂等。幂等表、发件箱和确认时机在消息队列那一节。

HTTP 调用和 Redis 调用放在这个事务外面。行锁占到提交为止，中间夹一次外部调用，会挡住别人改同一行库存。

秒杀是这条路径的变体，请求里不建单。活动库存预先写进 Redis，限购和扣减放在同一段 Lua 里，成功后把用户、SKU、请求 id 放进队列，消费者按数据库能承受的速度建单。订单表用用户加活动加 SKU，或用请求 id 做唯一键。脚本和拆段在系统设计的秒杀一节。普通下单仍在请求里把订单和发件箱一起提交。

#### 数据库

MySQL 存必须对的数：订单、库存、发件箱。连接从 HikariCP 取，请求结束归还。一个 JVM 一份池。总数是副本数 × `maximum-pool-size`，要小于 `max_connections` 的八成，寿命和探活在 HikariCP 那一节。

Redis 存可以重建的数：预扣的库存计数，以及后面会写上的详情缓存。Redis 丢了，用 MySQL 重新灌。MySQL 里没有的订单，就是没有这张单。

提交成功后删掉这张订单详情的 Redis 键。下一次读从 MySQL 回填。删失败就记一条待删，后台再删。键上的 TTL 是最后一道。先删缓存再被旧值回填的竞态，在缓存那一节。

队列里的消费者晚于事务。用户已经看到订单号时，搜索里可能还没有这条。详情页读 MySQL，或读刚从 MySQL 回填的缓存。

### 读：打开订单详情

```text
浏览器 GET /orders/{id}
  → 网关：验令牌、限流
  → 控制器：Caffeine → Redis → 主库
  → 用行里的用户 id 对请求头，再返回 JSON
```

#### 前端

详情页用订单号发 GET。登录态和请求 id 与写请求同一套头。GET 可以安全重试。页面先保留上一份已经拿到的数据，新的响应到了再替换。未登录去登录页。订单不存在，停在说明页。

#### 后端

网关同样验令牌和限流。服务先取出这一行，再用行里的用户 id 对请求头里的用户。对不上就拒绝。订单号被猜中也一样。

读的顺序固定。先看本进程的 Caffeine，秒级 `expireAfterWrite`。没有再看 Redis，分钟级 TTL。都没有再查主库，查到后回填两级缓存。缓存里放的是行，鉴权在读出之后。Caffeine 的 `get(key, loader)` 在一个 JVM 里同一个键只跑一次加载。多个进程同时过期，靠 Redis 锁。

查无此单可以短缓存。这把键只表示这个 id 没有行。不属于这个用户的拒绝不写进这把共享键，下一个用户不会因此看到别人的拒绝。

#### 数据库

这次读到的行来自 InnoDB 已提交的版本。普通 `SELECT` 不加锁。缓存键带业务版本，例如 `order:v3:{id}`。字段结构变了就换前缀，旧键等 TTL 自己消失。

刚提交完跳到详情的这次 GET 打主库。从库还没追上时，读副本会把刚写下的订单说成不存在。之后的普通详情可以接受秒级旧缓存。

每个 JVM 有自己的一份 Caffeine。订单在别的进程里更新之后，本进程要等本地 TTL 结束才去看 Redis。所以本地用秒，Redis 用分钟。

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

业务行存在 InnoDB 里。驱动负责连接、事务和 SQL。应用侧的入口是 MyBatis 的 Mapper，连接池是 HikariCP。MySQL 8 的库、表、连接都用 `utf8mb4`。名为 `utf8` 的字符集是 `utf8mb3` 的别名，最多三字节，生僻字和 emoji 会在写入时失败。查询缓存已在 8.0 移除，旧答案里的 Query Cache 不再存在。

### 一条语句怎么走进引擎

连接进来先做认证。MySQL 的经典模型是一个连接一个线程，`max_connections` 同时限制线程数和内存。每个连接会带排序缓冲、连接缓冲和临时表空间，规格要按连接数预留，不能只看缓冲池。

分析器做语法分析和名字解析。优化器决定索引、连接顺序和是否下推条件下推。执行器按这个计划调用存储引擎。`EXPLAIN` 看到的是优化器的计划，`EXPLAIN ANALYZE`（8.0.18 起）能看到实际行数和耗时。

InnoDB 的数据页默认 16KB。Buffer Pool 按页缓存数据和索引。专用机器上缓冲池可以占物理内存的一半到七成；容器里按 memory limit 算，给连接、临时表、操作系统和页缓存留余量。`innodb_buffer_pool_size` 按宿主机内存去设，容器一限制内存就会被 OOM 杀掉。

缓冲池用变种 LRU。新读入的页先放在 old 区（约 3/8，`innodb_old_blocks_pct`），只有停留超过 `innodb_old_blocks_time` 再被访问才进 young 区。全表扫描因此不会把热点页都挤出去。页被改过就成脏页，由后台刷盘。刷盘跟不上时，用户线程会被迫参与刷页，语句延迟抬高。

改一行时 InnoDB 做三件事。把旧版本推进 undo，形成版本链，供 MVCC 和回滚使用。把页的修改记进 redo，按 WAL 先写日志再刷脏页。把这次变更记进 server 层的 binlog，供复制和恢复使用。redo 是物理逻辑日志，崩溃恢复用它把页补到提交后的状态。undo 是逻辑上的旧版本。binlog 是 server 层的逻辑日志。

二次索引的页不在缓冲池里时，修改可以先记在 change buffer，空闲时再合并。唯一索引要立刻检查唯一性，用不上这条路径。脏页写回数据文件之前会先写 doublewrite，避免半页写入把页撕坏；redo 恢复要基于完整的页。

redo 先写 Log Buffer，再按 `innodb_flush_log_at_trx_commit` 刷盘。`1` 是每次提交都 fsync，提交返回后崩溃不丢这批事务。`2` 是每次提交写到操作系统缓存、大约每秒 fsync。`0` 是大约每秒才写日志。生产用 `1`。这个参数只管 redo。8.0.30 起 redo 容量用 `innodb_redo_log_capacity`，不再靠 `innodb_log_file_size` 乘文件个数。

binlog 刷盘由 `sync_binlog` 控制。`1` 是每次提交都 fsync。和 redo 的 `1` 一起用，一台机器上的已提交事务在进程崩溃后还在。组提交把多个事务的 fsync 合并。

事务提交在引擎和 binlog 之间是内部两阶段提交：

1. InnoDB 把事务标成 prepare，对应的 redo 落盘。
2. 写 binlog 并 fsync。
3. InnoDB 把事务标成 commit。

崩溃发生在 binlog 落盘之前，恢复时回滚这笔事务，从库也不会看到它。崩溃发生在 binlog 已经落盘、引擎 commit 标记还没写完时，恢复时根据 binlog 把引擎提交完成。只开 redo、不开 binlog，主从会分叉。

binlog 格式用 `ROW`，`binlog_row_image=FULL`。ROW 记的是行的前后像，复制和 Canal 订阅都依赖它。STATEMENT 记 SQL 文本，带不确定函数时主从会算出不同结果。`binlog_expire_logs_seconds` 控制保留时长，保留太短会让断线的从库补不上增量。

### 索引

InnoDB 用 B+ 树。非叶子节点只存键，叶子才有记录，相邻叶子串成链表，范围扫描沿着链表走。一棵树通常三到四层。二叉树在有序插入时会退化，红黑树对磁盘页不友好，哈希等值快但做不了范围。

主键索引的叶子是整行，这是聚簇索引。二级索引的叶子是索引列加主键。查完整行要拿主键再查一次聚簇树，这是回表。不建主键时，InnoDB 会找一个非空唯一列，找不到就生成隐藏的行 id。隐藏行 id 是全局分配的，并发插入会争用。主键用自增整数：插入多发生在最右边，页满了分裂出新页。随机主键（UUID v4）往页中间插，造成页分裂和碎片。需要在应用里生成、又希望按时间局部有序时，用雪花 id 或 UUID v7。

联合索引按定义顺序排序。第一列相同才比较第二列。查询从左往右用，中间断了，右边的列不能再用来缩小查找范围，但可以留在索引里做过滤。`(name, age, position)` 上，`name = ? AND position = ?` 只用到 `name` 做查找，`position` 在 5.6 之后可以索引下推：存储引擎在索引里顺手把 `position` 比掉，再回表。`Extra` 里是 `Using index condition`。范围条件的右边列同样不能做查找。

覆盖索引是查询的列都在这棵索引里，`Extra` 出现 `Using index`，不再回表。`SELECT *` 加大范围时，优化器经常放弃二级索引，因为每一行都要回表。这时把列收窄，计划就会回到索引上。`FORCE INDEX` 能逼它走，代价不一定更低。

最左前缀包括三种用法：从第一列开始的等值或范围；第一列的最左字符串前缀，`LIKE 'LiLei%'` 能用，`LIKE '%LiLei'` 不能用这棵索引做查找；第一列等值时，后面的列接着用。索引列上包函数、做计算、隐式类型转换，这列的查找能力就没了。字符串和数字比较时，优化器可能把索引列转成数字，整棵索引都用不上。

`EXPLAIN` 看 `type`、`key`、`key_len`、`rows`、`Extra`。`type` 从好到差大致是 `const`、`eq_ref`、`ref`、`range`、`index`、`ALL`。`const` 是主键或唯一键等值命中一行。`eq_ref` 是连接时被驱动表按唯一键最多一行。`ref` 是非唯一索引等值。`range` 是索引上的范围。`index` 是扫整棵索引的叶子。`ALL` 是全表。`key_len` 看出联合索引用了前几列，计算时把字符集字节数、变长长度和 NULL 标记算进去。`rows` 是估算。`Using filesort` 表示排序没沿索引顺序走。`Using temporary` 表示去重或分组用了临时表。

`id` 相同的是同一层，数字更大的子查询先执行。`select_type` 里 `SIMPLE` 是单表或简单连接，`PRIMARY` 是最外层，`SUBQUERY` 是标量子查询，`DERIVED` 是 `FROM` 里的子查询，会先落成临时表。`filtered` 是估算里还能留下的百分比，和别的表连接时用 `rows * filtered / 100` 估这一侧的行数。`EXPLAIN ANALYZE`（8.0.18 起）看实际行数和耗时。

`key_len` 按字符集算。`utf8mb4` 下一个字符最多 4 字节。`varchar(n)` 还要加长度前缀：这一列的最大字节数不超过 255 时是 1 字节，否则是 2 字节。列允许 NULL 再加 1 字节。8.0 默认 DYNAMIC 行格式，单列索引前缀上限是 3072 字节，更长的字符串只能索引前缀。更早的资料里 768 字节、一个汉字 3 字节，对应的是 COMPACT 和名为 `utf8` 的 `utf8mb3`。

范围很大又必须回表时，优化器会放弃二级索引。`SELECT *` 加一个宽范围经常是这种计划。改成只取索引里的列，变成覆盖索引，计划会回到索引上。要看它比较了哪些代价，打开这一会话的 `optimizer_trace`，执行语句，再查 `information_schema.OPTIMIZER_TRACE`。

分页用上一页的游标：`WHERE id > ? ORDER BY id LIMIT 10`。`LIMIT 100000, 10` 会先扫过前面的行再丢掉。按非主键排序时，先在子查询里只取出排序后的主键，再按主键回表拿整行。

有索引的等值连接多是嵌套循环，被驱动表走索引，小表做驱动，循环次数才小。`INNER JOIN` 里谁驱动看 `EXPLAIN`。没索引时，更早的版本走块嵌套循环：驱动表按 `join_buffer` 分块，每块和被驱动表全表比。8.0 对这种大表连接经常改用哈希连接，用较小的一侧建哈希表。`IN` 适合外表大、子查询结果小。`EXISTS` 适合外表小。两边连接列都有索引时，优化器常常把它们改写成连接。

`count(*)` 和 `count(1)` 都是数行，InnoDB 会选一棵最窄的索引来扫。`count(列)` 要跳过 NULL，不能用这列不在里面的索引。`count(主键)` 往往扫聚簇索引，行更宽。引擎不保存一张表的精确总行数。MyISAM 对不带 WHERE 的 `count(*)` 直接读自己维护的计数，但没有事务和行锁，业务表用 InnoDB。`SHOW TABLE STATUS` 的 `Rows` 是估算。精确总数如果很贵，就维护在别的存储里，并接受延迟。事务回滚不会自动改那份计数。慢查询打开 `slow_query_log`，阈值收到几百毫秒，再配合 `EXPLAIN`。

### 事务、隔离与锁

ACID 落在不同的机制上。原子性靠 undo。持久性靠 redo 和 binlog 的刷盘。隔离性靠锁和 MVCC。一致性是业务约束，由事务边界、约束和外键一起保住，例如扣款和加款在同一个事务里。

并发现象有四种。脏写是两个事务都按自己读到的旧值去写，后写的覆盖先写的。脏读是读到别人还没提交的修改。不可重复读是别人提交之后，本事务再读同一行，值变了。幻读是同一范围再读，行集合变了。

四个隔离级别：读未提交、读已提交、可重复读、串行化。InnoDB 默认可重复读。读已提交解决脏读。可重复读在快照读上再解决不可重复读，当前读配合临键锁把幻读也挡住。串行化把普通 `SELECT` 变成加共享锁的当前读。

MVCC 用 undo 版本链和 Read View。行上有隐藏的 `trx_id` 和 `roll_pointer`。普通 `SELECT` 是快照读，不加锁。可重复读在事务第一次一致性读时生成 Read View，之后普通 `SELECT` 都用这一份。读已提交每次一致性读都生成新的 Read View。`BEGIN` 本身不生成视图，事务里还没读过就去更新，看的是当前读。

Read View 里有四项。`creator_trx_id` 是当前事务自己的 id。`m_ids` 是生成视图时还活跃的事务 id。`min_trx_id` 是其中最小的那个，源码字段是 `m_up_limit_id`，比它小的事务在视图生成前已经提交。`max_trx_id` 是生成视图时系统即将分配的下一个 id，源码字段是 `m_low_limit_id`，大于等于它的写入发生在视图之后。

拿一行的 `trx_id` 做判断，不可见就顺着 `roll_pointer` 找更老的版本，直到可见或链尽头：

1. 等于 `creator_trx_id`：自己写的，可见。
2. 小于 `min_trx_id`：视图生成前已提交，可见。
3. 大于等于 `max_trx_id`：视图生成后才开始的事务，不可见。
4. 落在两者之间：在 `m_ids` 里说明生成视图时还没提交，不可见；不在 `m_ids` 里说明当时已经提交，可见。

`SELECT ... FOR UPDATE`、`FOR SHARE`、`UPDATE`、`DELETE` 是当前读。它们读最新已提交版本并加锁。可重复读下，当前读对索引记录加临键锁：记录锁加这条索引记录前面的间隙锁，区间左开右闭。间隙锁挡住别人往这个范围里插行。快照读靠 Read View 本来就看不到事务第一次一致性读之后提交的新行。

同一个事务里如果先做了快照读，再做当前读，当前读仍可能看见并修改别人刚刚提交的新行。可重复读挡住的是「同一条快照读语句前后看到的行集合」，当前读走的是另一条路径。库存和余额用单条条件更新，不依赖先 `SELECT` 再在 Java 里减。

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

```java
@Transactional(rollbackFor = Exception.class, isolation = Isolation.READ_COMMITTED)
public void deduct(long skuId) {
    int rows = inventoryMapper.deduct(skuId);
    if (rows == 0) {
        throw new StockNotEnoughException(skuId);
    }
}
```

```xml
<update id="deduct">
  UPDATE inventory
  SET stock = stock - 1
  WHERE id = #{id} AND stock > 0
</update>
```

`rows == 0` 就是没扣成。这一条语句本身是原子的。乐观锁是另一种做法：读出 `version`，更新时带 `WHERE version = ?`，同时把版本加一。适合冲突少的行。冲突很高的热点行，重试会打满数据库，热点要移到前面的 Redis 或队列里。

死锁是两笔事务各自占着对方下一步需要的锁。InnoDB 检测到环之后回滚代价较小的那一笔。锁等待超时是 `innodb_lock_wait_timeout`（默认 50 秒）到了还没拿到锁。MySQL 错误码 1213 是死锁，1205 是锁等待超时。

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

两条 `SELECT` 都成功。真正插入时，插入意向锁和对方的间隙锁冲突，两边都在等一个还不存在的唯一键。InnoDB 回滚其中一笔，应用看到 1213 后重试整笔事务。先插入、靠唯一键冲突去重，可以不先做这句 `FOR UPDATE`。范围更新按同一顺序加锁，例如都按主键从小到大，环会少很多。Spring 把它们翻成 `CannotAcquireLockException` 一类的数据访问异常，应用把整笔事务重试，设次数上限和抖动。锁的顺序按主键固定。事务里只留必须原子的 SQL。HTTP、Redis 和发消息放在事务外面。行锁会一直占到提交，外部调用一慢，就挡住别人。

长事务还有另一条事故链：它占着 MDL 和 undo。undo 回收不掉，历史链表变长，查询要沿着版本链找自己的 Read View。这时先查 `information_schema.innodb_trx`，把跑了几分钟的事务杀掉，再谈加索引。

### 复制与高可用

主库把 binlog 发给从库，从库有 IO 线程接收、SQL 线程重放。异步复制是主库写完自己的日志就返回。主库在这一瞬间宕机，已提交的事务可能不在从库上。

半同步要求至少一个从库确认收到日志，主库才给客户端返回。等待点是 `rpl_semi_sync_source_wait_point`。默认 `AFTER_SYNC`：binlog 已经 fsync，从库确认写进 relay log 之后，主库才在存储引擎里提交并响应客户端。`AFTER_COMMIT` 先在主库引擎提交再等从库，提交之后、确认之前，别的会话已经能读到这行。`rpl_semi_sync_source_timeout` 超时后退回异步。生产保持 `AFTER_SYNC`。故障转移之后按新主重建旧主。

GTID 用 `source_id:transaction_id` 标识每一笔已提交事务。从库按 GTID 集合续传，换主时不用手工对文件名和位点。

从库可以承担读。写之后立刻读自己的写入，要打主库，或者在从库上 `WAIT_FOR_EXECUTED_GTID_SET` 等到这笔 GTID 重放完再读。延迟的常见来源是从库单线程重放、大事务、从库机器更弱、锁等待。并行复制按库或按 writeset 并行。大事务拆开。

`readOnly = true` 只是让驱动在这条连接上声明只读事务，Hikari 和 Spring 都不会因此把语句送到副本。读写分离要自己做路由：`AbstractRoutingDataSource` 按事务是否只读选择数据源，或者把路由交给代理。刚写完的查询继续走主库。

组复制（MGR）用 Paxos 在多数节点上提交事务，单主模式下写仍进主节点。它解决的是复制本身的高可用。代理或编排负责把写流量切到新的主。切换期间会有一小段不可写，客户端要重试幂等的语句。

只读实例把 `read_only` 和 `super_read_only` 打开。备份用物理备份加 GTID，逻辑备份适合小库和单表。恢复演练按固定周期做。

### HikariCP、JDBC 与 MyBatis

Spring Boot 默认的池是 HikariCP。一个请求一个 `SqlSession`，请求结束就关。`SqlSession` 不是线程安全的。`SqlSessionTemplate` 用 JDK 动态代理把每次调用绑到当前线程：有 Spring 事务时复用事务里的那一个会话，没有事务时用完即关。把 `SqlSession` 放进单例字段，会把连接和缓存串到下一次请求。

```yaml
spring:
  datasource:
    url: jdbc:mysql://mysql:3306/app?sslMode=REQUIRED&characterEncoding=UTF-8&rewriteBatchedStatements=true&cachePrepStmts=true
    hikari:
      maximum-pool-size: 20
      minimum-idle: 10
      connection-timeout: 300ms
      max-lifetime: 1740000
      keepalive-time: 60000
      leak-detection-threshold: 5000
```

`connection-timeout` 是从池里借连接的等待上限，默认 30 秒。池耗尽时，Tomcat 线程会在这里堵住，接口超时要短于网关超时，才能把「池空了」变成明确的失败。`maximum-pool-size` 默认只有 10。总数是副本数 ×（`maximum-pool-size`），要小于 `max_connections` 的八成左右，给管理连接、复制和在线变更留位置。

`minimum-idle` 默认等于 `maximum-pool-size`，这时 `idle-timeout` 不生效，池会一直维持满编。把 `minimum-idle` 调低，空闲连接才会被收回。`max-lifetime` 默认 30 分钟，必须短于数据库 `wait_timeout`、云负载均衡和防火墙的空闲断开时间，并留出几分钟余量。Hikari 会在这个寿命上加一点抖动，避免整池同时重连。`keepalive-time` 在空闲连接上发探测，最短 30 秒，用来对付「连接在池里是好的、对端已经掐了」的半开连接。故障转移之后，下一次借出前的探测能把死连接丢掉。

`leak-detection-threshold` 大于 0 时，连接借出超过这个时间还没还，Hikari 打一条栈。阈值要大于最慢的正常事务，否则正常的批处理会被当成泄漏。

`rewriteBatchedStatements=true` 把 JDBC 批处理改写成一条多值 `INSERT`。它在客户端预编译时生效；`useServerPrepStmts=true` 走服务端预编译时，这层改写经常不起作用。批量写入保持客户端预编译，再加上 `cachePrepStmts=true`。MyBatis 默认的执行器是 `SIMPLE`，要批处理就在 `ExecutorType.BATCH` 的会话里 `flushStatements`，更新计数要等刷出之后才完整。

`#{}` 是 `PreparedStatement` 的占位符。`${}` 是字符串拼接，只允许来自白名单的列名、表名，用户输入走它就是注入。

一级缓存是 `SqlSession` 级别，默认 `localCacheScope=SESSION`。同一次事务里再次查询同一条语句，MyBatis 直接返回缓存的对象，SQL 不会发出去，隔离级别也挡不住这份对象。没有事务时，`SqlSessionTemplate` 每次调用新建会话，缓存随会话关闭。需要在事务中看见别人刚提交的更新时，把作用域改成 `STATEMENT`，或在这条查询上 `flushCache`。二级缓存按 Mapper 命名空间跨会话共享，分布式部署里各进程一份，和 Redis 重复，生产通常关掉。

更新在默认执行器里会清空本会话的一级缓存。`PageHelper` 把分页参数放在 `ThreadLocal`，必须紧挨着目标查询调用。中间插了一次权限查询或计数查询，分页会套到那条 SQL 上。

列表接口避免 N+1。主查询之后按关联键一次 `IN` 查回来，在 Mapper 里写清楚，或者用嵌套查询配上延迟加载的边界。循环里对每行 `selectById` 会把一次列表变成几百次查询。

`@Transactional` 的传播、回滚和自调用放在框架那一节。这里只固定和连接有关的两件事：隔离级别在事务开始时设到这条连接上，中途改不了已经开始的事务；只读标志不会自动走到从库。

迁移放在滚动更新之前的一次性 Job 里跑。多个副本同时改同一张表会抢 MDL。8.0 里不少加列可以用 `ALGORITHM=INSTANT`，做不到就让语句失败。改列类型、加二级索引仍可能重建或长时间占用。大表的阻塞变更用 gh-ost、pt-online-schema-change 在线做。变更期间的 binlog 量会上升，从库延迟要一起看。

### 分库分表

先确认单机的索引、SQL、连接数、缓冲池和磁盘已经用尽，再拆。垂直拆按业务分库，订单和用户各自的库。水平拆按用户 id 或订单 id 取模或按范围。

两条接入方式：

- 代理（ShardingSphere-Proxy、Vitess、ProxySQL）对外说 MySQL 协议。应用拿普通连接串连代理，分片规则在代理上。
- 应用内分片：ShardingSphere-JDBC 是一个 JDBC 驱动，SQL 在应用进程里改写。少一跳网络，分片规则和驱动版本跟着应用发布。分片算法、SQL 解析和路由按 SPI 插进去，规则配在逻辑表和分片键上。

分片键选查询一定会带上的列。按用户 id 拆之后，按订单 id 查就要有一张订单 id 到用户 id 的映射，或者订单 id 里就编进用户 id。范围查询和时间排序会扇出到所有分片，这种查询改走搜索引擎或汇总库。扩容用成倍的分片数，数据只需要搬一半。迁移期间双写或按 id 段搬，对完数再切路由。跨分片的写入用消息做最终一致。

全局唯一约束在单表里由唯一索引保证。分片之后，唯一键必须包含分片键，否则要单独建一张以这个键为分片键的索引表。

---

## 2. Redis 与缓存

Redis 在这条体系里做四件事：缓存、计数、分布式锁、原子脚本。Spring Boot 默认的客户端是 Lettuce，基于 Netty，一个连接上可以同时在途多条命令。Jedis 是阻塞客户端，一个线程一次借一条连接，池的大小就是并行度。新代码用 Lettuce；已经用 Jedis 把池大小算进 `maxclients` 即可。Redisson 在这两者之外提供锁、布隆过滤器和限流结构，底层仍是 Redis 协议。

应用关闭时让 `LettuceConnectionFactory` 跟着上下文销毁。Redis 7 起，小哈希、小有序集合和列表 quicklist 的节点用 listpack。Stream 可以做单机的轻量日志，堆积受内存限制，跨服务的事件总线仍用后面的消息队列。

### 命令执行

一条命令的执行在主线程上排队。快是因为数据在内存里，数据结构把操作控制在可预期的复杂度，加上 epoll 把「连接可读」变成事件。慢命令会堵住后面所有命令：`KEYS`、对大 key 的 `SMEMBERS` 或 `HGETALL`、对大集合的 `SUNION`。`io-threads` 默认是 1，也就是只有主线程。把它设成大于 1 之后，多出来的线程默认只写响应，`io-threads-do-reads yes` 才让它们也读。`io-threads 4` 表示 1 个主线程加 3 个 IO 线程。命令本身仍在主线程执行。`UNLINK` 和过期删除可以丢给后台线程释放内存。

key 在服务器里是 SDS，可以存二进制，长度单独存放。值用 JSON 或纯数字，键名带业务和版本：`order:stock:v1:{sku}`。过期时间在写入时一起设上，`SET key value EX 秒`。先 `SET` 再 `EXPIRE` 的话，两条命令之间进程挂了，键就没有过期时间。

过期有两条清理路径。访问到一个已经到期的键时顺手删掉，这是惰性删除。后台定时随机抽一批带过期时间的键，删掉其中到期的。到期不代表内存立刻下降。内存上限靠 `maxmemory` 和淘汰策略，和过期是两套机制。

Lettuce 默认和 Spring 共享连接，多路复用把多个线程的命令写进同一条连接。Redis 服务端仍是单线程执行。`BLPOP` 这类阻塞命令会占住连接，连接工厂会为它们单独借出连接。要让阻塞式 `RedisTemplate` 真正并行，再加上 commons-pool2，并设置 `spring.data.redis.lettuce.pool.enabled=true`。池的上限同样要小于 `maxclients`。

`RedisTemplate` 默认用 JDK 序列化，key 和 value 在 Redis 里不是原文，别的语言和其他服务都读不了。业务缓存用 `StringRedisTemplate`，值自己写成 JSON。`GenericJackson2JsonRedisSerializer` 会把 `@class` 写进 JSON，反序列化不可信的内容时能被利用来构造任意类型。缓存值用明确的 DTO，或者纯字符串。

超时要拆开。连接超时管握手，命令超时管一条命令最多等多久。超时后这条命令的结果不确定，服务器可能已经执行了 `INCR`。扣库存靠 Lua 的返回值判断，超时就用请求 id 再查一次。

### 结构

按过期粒度和内存选类型：

| 类型 | 用法 | 编码 |
| --- | --- | --- |
| 字符串 | 计数用 `INCR`，对象用 JSON。每个 key 都能单独过期 | int、embstr、raw |
| 哈希 | 一个对象的多个字段。过期只能设在整个 key 上 | 字段少用 listpack，多了转哈希表 |
| 列表 | 队列、时间线 | quicklist，节点是 listpack |
| 集合 | 去重 | 全是整数且数量不大时用 intset，否则哈希表 |
| 有序集合 | 排行榜、延迟任务 | 成员到分数用哈希表，按分数有序用跳表。量很小用 listpack |
| Stream | 带 id 的追加日志，消费组、待确认列表 | listpack 组成的 radix tree |
| 地理位置 | `GEOADD` / `GEOSEARCH` | 分数是 geohash，底层是有序集合 |

哈希表冲突用链表，负载高了做渐进式 rehash：读写某个桶时顺便把旧表往新表搬。跳表从有序链表抽出稀疏的上层，查询从顶层往下跳，范围扫描沿着底层走。有序集合同时要按成员查分数和按分数扫范围，所以是哈希表加跳表。

管道把多条命令一次写出。`RedisTemplate.executePipelined` 拿到的是结果列表。管道里某一条失败，其他命令仍然执行。`MULTI`/`EXEC` 把一组命令收成一个事务，中途失败的处理取决于命令本身，Redis 事务没有回滚。管道和事务都做不到「先读再按结果写」的原子。这种读改写用 Lua。`WATCH` 加上 `MULTI` 是乐观锁，写之前键变了就整事务失败，由客户端重试。Lua 在主线程上原子执行，脚本里不要写循环去扫大集合。

Redis 7 的 Function 把脚本登记在服务器上，随复制持久化。`DefaultRedisScript` 走 `EVAL`，脚本正文跟应用版本走。无论哪一种，脚本要短，key 从 `KEYS` 传入，这样集群才能检查槽位。

### 连接、内存与超时

大键用 `MEMORY USAGE` 或 `redis-cli --bigkeys` 找。删除大键用 `UNLINK`。线上扫键用 `SCAN`，游标由服务端保持，中间有写入时不保证不重复、不遗漏，调用方自己去重。`maxmemory` 设了之后要选淘汰策略：

| 策略 | 行为 |
| --- | --- |
| `allkeys-lru` | 在所有键里淘汰最近最少使用的 |
| `allkeys-lfu` | 在所有键里淘汰使用频率最低的，计数会随时间衰减 |
| `volatile-ttl` | 只在设了过期时间的键里淘汰更快过期的 |
| `volatile-lru` / `volatile-lfu` | 只淘汰设了过期的键 |
| `noeviction` | 写满后拒绝写入 |

缓存用 `allkeys-lfu` 或 `allkeys-lru`。访问分布很斜时 LFU 更合适。`volatile-*` 只在带 TTL 的键里挑，如果这些键的内存远小于 `maxmemory`，内存仍会顶满，然后写失败。`noeviction` 适合把 Redis 当锁和计数的存储、不能丢键的场景。拒绝写入时，应用要把 Redis 故障和「键不存在」区分开，否则会把一次内存满当成缓存未命中，全部打到数据库。

Lettuce 收到 `MOVED` 会更新槽表并跳到持有该槽的节点，`ASK` 只对这一次请求临时跳转。Spring Boot 里 `spring.data.redis.lettuce.cluster.refresh.adaptive` 默认是 false，`period` 默认是空。两个都不打开时，拓扑变化主要靠下一条命令的重定向。主从切换之后、下一条命令到来之前，连接可能还指着旧主。生产把 adaptive 打开，并设一个 30 秒量级的 period：

```yaml
spring:
  data:
    redis:
      lettuce:
        cluster:
          refresh:
            adaptive: true
            period: 30s
```

Lettuce 7 自身把自适应触发器改成了默认全开。用 Spring Boot 时以 Boot 装配出来的 `ClusterTopologyRefreshOptions` 为准，把这两项显式写进配置，不依赖客户端默认值。

### 持久化、复制、哨兵、集群

RDB 是某一时刻的全量二进制，恢复快，生成慢。`bgsave` 用 fork 和写时复制，父进程继续服务。写得多时，拷贝被改过的页，父进程内存会涨上去。AOF 追加命令，`everysec` 大约每秒 fsync，最多丢一秒。`always` 每条命令都 fsync。重写把一串 `INCR` 收成一条 `SET`。4.0 起混合持久化在重写时写成 RDB 头，之后的新命令仍追加 AOF。Redis 7 的 AOF 可以拆成多个文件。缓存可以只开 RDB，甚至不持久化，数据能从数据库重建。锁和限购如果只活在 Redis 里，就要开 AOF，并接受最多大约一秒的丢失窗口。

主从第一次同步是全量 RDB，加上复制缓冲区里的增量。断线后用 backlog 里的 offset 续传，offset 已经被覆盖就退回全量。所有从节点同时找主做全量，会把主打满。从节点下面再挂从节点。Redis 的复制是异步的。

哨兵监视主节点。quorum 是多少个哨兵同意才算主失效，一般是哨兵数的一半加一。真正执行故障转移还要有多数哨兵可达。客户端用 `RedisSentinelConfiguration`，地址写死成某一台，切换之后会一直打到已经下线的主。故障切换有几秒到十几秒不可写。从节点复制有延迟。扣减、锁、限购走主节点。

Cluster 把数据分成 16384 个槽，`CRC16(key) % 16384`。跨槽的多 key 命令会被拒绝，除非用哈希标签 `{sku}` 让相关 key 落在同一槽。事务、管道和 Lua 里的多个 key 也受这个限制。节点用 gossip 交换状态。某个槽的主从都不可用时，这个槽上的键不可用；`cluster-require-full-coverage` 为 yes 时，整个集群拒绝写入。

### 锁

单机上加锁是一条命令：`SET key token NX PX 毫秒`。token 是随机值。释放时只有值仍是自己的 token 才 `DEL`，用一段 Lua 比较再删。两条命令拆开做，会把别人刚拿到的锁删掉。

Redisson 的 `RLock.lock()` 不指定租约时会开看门狗，默认 30 秒，大约每 10 秒续一次，进程还活着就把锁延长。`tryLock(waitTime, leaseTime, unit)` 一旦写了 leaseTime，看门狗就不再续期，业务超过租约之后锁会消失，第二个人进得来。等锁用只带等待时间的那个重载：

```java
RLock lock = redisson.getLock("lock:stock-load:" + skuId);
if (!lock.tryLock(200, TimeUnit.MILLISECONDS)) {
    throw new BusyException();
}
try {
    loadFromDatabase(skuId);
} finally {
    if (lock.isHeldByCurrentThread()) {
        lock.unlock();
    }
}
```

`timeout` 是锁的寿命，业务必须在这之前结束，或者依赖看门狗。进程被杀死后续期也停了，锁会在 TTL 后释放。业务耗时和锁超时重叠时，两个人可能都以为自己持有锁，所以临界区再检查一次资源状态。`unlock` 只能由持有者调用，当前线程没持有就去解，会抛错，也会打乱别人的重入计数。

主从切换时锁可能丢：写在主上，还没复制到从，主挂了，新主没有这把锁。看门狗用的是 Redis 服务器的过期时间，客户端时钟跳了不会直接让这把锁提前过期。多主投票的 Redlock 要求多个独立主节点、时钟偏差有上界、实例会在超时后放锁。网络延迟和时钟跳变会破坏这些假设。库存扣减用 Lua 直接改数字。锁只包住「从数据库装载库存进缓存」这种重建过程。真正不能丢的互斥，用数据库唯一键或行锁。

### 缓存

详情页拆成能缓存的部分和必须实时的部分。标题、图、属性、规格可以缓存。价格、库存、促销单独接口，缓存时间短，或者直接读 Redis。扣库存和支付结果以数据库事务为准。

整页静态化在发布时用模板渲染成 HTML，放到对象存储或 Nginx 能直接读的目录。模板一改要重渲染全部商品。商品一多就改成 Nginx 缓存 HTML 片段，实时数据走接口。带登录态的页面按用户片段缓存，避免把一个人的页面交给下一个人。

本地缓存挡在 Redis 前面。Caffeine 设 `maximumSize` 和 `expireAfterWrite`。每个 JVM 有自己的一份，失效不会自动广播。本地 TTL 用秒级，Redis TTL 用分钟级。没有失效通知时就靠短 TTL。要主动失效，就在更新后发一条 Redis Pub/Sub；Pub/Sub 不留存，进程刚启动时本地是空的，第一批请求打到 Redis。Redis 6 的客户端缓存可以在键变更时收到失效通知，Lettuce 能打开 tracking。

三类失效：

- 击穿：热点 key 过期，大量请求同时打到数据库。用锁只放一个请求去加载，其他请求短暂等待或返回旧值。`@Cacheable(sync = true)` 的互斥只在当前这一个进程里。
- 穿透：查不存在的 key。固定的空值可以缓存，TTL 更短。每次都换 key 时，用布隆过滤器先挡。Redisson 的 `RBloomFilter` 或 RedisBloom 的 `BF.ADD` / `BF.EXISTS` 都可以。过滤器说不存在就一定不存在；说存在仍可能是误判，那时再查库。元素只能加不能删，删数据要换一个新过滤器再灌一遍主键。
- 雪崩：一批 key 同时过期，或 Redis 本身不可用。TTL 加随机抖动。热点 key 用锁重建。Redis 不可用时要有限流和降级，而不是无限穿透到数据库。

强一致的数据留在数据库里。缓存这条链路做到最终一致：先写库，事务提交成功后再删 Redis。删缓存放在 `afterCommit`，提交前删除会让并发的读把旧行又装回缓存。

```java
@Transactional(rollbackFor = Exception.class)
public void updateDetail(long id, Detail detail) {
    detailMapper.update(detail);
    TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
        @Override
        public void afterCommit() {
            stringRedisTemplate.delete("detail:v3:" + id);
        }
    });
}
```

删失败就记一条待删记录，后台再删，TTL 是最后一道。直接改缓存里的对象要处理局部更新和并发写，容易和数据库岔开。

有一个固定的竞态：请求 A 读到旧值，请求 B 写库并删掉缓存，A 再把旧值写回去。处理是写路径只删不改，读路径回填时带短 TTL；写完隔一小段再删一次，间隔要盖住正在回填的读。更稳的是订阅 binlog，提交已经发生之后再删缓存。

空值也缓存。布隆过滤器放在缓存之前，元素是已经存在的主键。误判率大约是 `(1 - e^{-kn/m})^k`：`m` 是位数，`k` 是哈希次数，`n` 是预计元素个数。超过预计容量，误判率会很快变高。

缓存键带上业务版本，例如 `detail:v3:{id}`。结构变了就换前缀，旧键等 TTL 自己消失。展示用的总库存可以短暂不准，放 Redis 并加随机 TTL。

---

## 3. 计算机网络

应用看到的是一条 HTTP 请求。它实际穿过网卡、IP 路由、TCP 连接、TLS、HTTP，再进入 JVM。Java 服务把 TLS 放在 Ingress 或 SLB，Tomcat 对内用 HTTP/1.1。超时、连接池和幂等要按这几层分开设。

### 分层和一次请求

链路层把帧送到同一二层网络里的下一跳。IP 层按路由表选下一跳，不保证到达。TCP 在 IP 之上提供字节流：可靠、有序、有拥塞控制。UDP 只提供报文。HTTP 是请求响应的应用协议，跑在 TCP 上；HTTP/3 跑在 QUIC 上，QUIC 跑在 UDP 上。

一次跨机器调用的时间大致是这些量级：同机房内存访问约 100 纳秒，同可用区网络往返约 0.5 毫秒，跨可用区约 1 毫秒，固态盘读约 100 微秒。应用超时如果设成和同机房 RTT 一样短，正常抖动就会失败；如果设成几十秒，故障时线程和连接会被占满。

DNS 把名字解析成地址。JVM 有自己的缓存，`networkaddress.cache.ttl` 在安全策略里常被设成永远缓存成功的解析。Kubernetes 里 Pod 的 IP 会变，TTL 设成几十秒，失败的解析用一个更短的 negative TTL。操作系统的 `resolv.conf` 常见 `ndots:5`：名字里的点少于 5 个时，先拼上搜索域再问绝对名字。`mysql.xxx.com` 这种外部名字也会先走一遍搜索域，多几次 NXDOMAIN，延迟就加在每次新建连接上。集群内服务用服务名，集群外地址用完整域名。连接池把「解析加握手」摊到很多请求上。

Java 的 `InetAddress` 解析会挡住调用线程。虚拟线程在这里可以卸载；平台线程会真的堵住。不要在 Netty 的 EventLoop 上做同步解析。

### TCP

三次握手：客户端发 SYN，服务端回 SYN+ACK 并进入 SYN_RCVD，客户端再发 ACK，两边进入 ESTABLISHED。服务端在握手完成前把连接放在 SYN 队列，完成之后放进 accept 队列，等 `accept` 取走。SYN 队列满了，新的握手会被丢掉或触发 SYN Cookie。accept 队列满了，已完成的连接会被丢掉，客户端看到超时。队列长度受 `somaxconn` 和监听 backlog 里较小的那个限制。`ss -lnt` 里，监听套接字的 Recv-Q 是还没被 accept 的连接数，Send-Q 是 backlog。

Tomcat NIO 在这两级队列之上还有自己的限额。Acceptor 接受连接，Poller 用 `Selector` 等可读，工作线程执行请求。`server.tomcat.max-connections` 默认 8192，是 Poller 登记的连接上限。到了这个数，Acceptor 停下来，新的已完成连接堆在 accept 队列里。`accept-count` 默认 100，就是这份 backlog。`threads.max` 默认 200，是真正执行业务的平台线程数。连接可以远多于工作线程：多数连接停在等下一个 keep-alive 请求。工作线程打满之后，请求在 Tomcat 内部排队，客户端看到的是读超时。打开虚拟线程之后，`threads.max` 不再限制请求并发，上限回到 `max-connections`、Hikari 和下游的舱壁。

四次挥手：主动关闭的一方发 FIN，对方回 ACK，之后对方发 FIN，主动方再回 ACK。主动关闭的一方进入 TIME_WAIT，等待 2MSL，用来让最后的 ACK 有机会重传，并让老的报文从网络里消失。TIME_WAIT 在主动关闭的那一端。HTTP 保活超时由服务端发起关闭时，TIME_WAIT 堆在服务端。它占的是四元组。`tcp_tw_recycle` 已经从内核里去掉。`tcp_tw_reuse` 只影响本机作为客户端向外发起的连接。

保活用来发现对方已经消失、但本机还以为连接在的情况。中间设备也会按空闲时间掐连接。应用的空闲超时、负载均衡的空闲超时、数据库的 `wait_timeout`、Hikari 的 `max-lifetime` 和 `keepalive-time` 要放在同一套数上：越靠近调用方，空闲断开越晚，池里的连接才不会被对端先关掉。

Nagle 把小包攒一攒再发，延迟 ACK 让接收方等一等再确认。两个同时开着，小请求会多等一个延迟 ACK 的定时器。内网 RPC 关 Nagle（`TCP_NODELAY`）。Netty 和 HTTP 客户端一般已经关了。

发送方实际能发出去的数据量取两个窗口里较小的那个。

流量控制保护接收方。接收方在 ACK 里通告 `rwnd`，大小是自己套接字缓冲还剩多少。缓冲满了就通告 0，发送方停住，之后用窗口探测（一个字节）问窗口有没有恢复。应用读得慢、Tomcat 工作线程或虚拟线程把字节堆在接收缓冲里，对端就会因为 `rwnd` 变成 0 而停发。这和连接数打满是两条线：一个是这条连接的接收缓冲，一个是文件描述符和线程。

拥塞控制保护网络。发送方自己维护 `cwnd`。慢启动把 `cwnd` 按指数放大，超过阈值后进入拥塞避免。丢包时 CUBIC 把窗口降下来再爬。BBR 按估算的带宽和 RTT 发，少依赖丢包当信号。`cwnd` 变小或丢包重传，应用看到的是长尾。同一可用区里的超时要能盖住偶发重传，跨地域调用要把超时和重试次数一起降下来。

接收方缓冲还很大、链路已经在丢包时，先被 `cwnd` 卡住。链路是空的、应用却不读 socket 时，先被 `rwnd` 卡住。

半开连接是一端已经重启，另一端还拿着旧连接去写。写才会发现 RST，只读的连接要靠超时或 keepalive。所以读超时是必须的。Hikari 的 `keepalive-time` 和 Lettuce 的命令超时都是在补这一层。

TCP 是字节流。一次 `read` 可能拿到半条消息，也可能拿到两条消息粘在一起。HTTP 用 `Content-Length` 或分块编码做帧边界，Tomcat 已经把帧拆好再交给 Spring。自己写的 Netty 协议要有长度字段或分隔符，`LengthFieldBasedFrameDecoder` 先拆帧，业务线程再处理完整消息。按一次 `channelRead` 当一条消息，就会在压力一大时解析错位。

### HTTP

HTTP/1.1 在一条 TCP 上串行请求和响应，靠 `Connection: keep-alive` 复用连接。管道化把多个请求一起写出，但服务器必须按顺序响应，实践里很少开。队头阻塞发生在这一层，也发生在 TCP 丢包时：一个包丢失，这条连接上后面的字节都要等重传。

HTTP/2 在一条 TCP 上多路复用多个流，头部用 HPACK 压缩。TCP 丢包时，这条连接上所有流仍然一起停。HTTP/3 把流放到 QUIC 上，一个流丢包只挡住这个流。QUIC 的 0-RTT 能在恢复连接时提前发数据，这段数据可以被重放，非幂等的 POST 不能放进 0-RTT。Spring Cloud Gateway 和 gRPC 常跑在 HTTP/2 上，一条长连接会被钉在选中的那个后端上。

方法按幂等来记。GET、HEAD、PUT、DELETE 在协议语义上是幂等的。POST 不是。实际接口里 PUT 和 DELETE 仍然可能有副作用，能不能重试看服务有没有按幂等实现。状态码按谁的责任分：

| 状态码 | 含义 |
| --- | --- |
| 200 / 201 / 202 / 204 | 成功、已创建、已接受但还没做完、成功且没有正文 |
| 400 | 请求不合法。`@Valid` 失败走这一档 |
| 401 / 403 | 没有身份、有身份但没有权限 |
| 404 | 资源不存在 |
| 409 | 和当前状态冲突，例如唯一键、幂等键对应了另一份请求体 |
| 429 | 被限流 |
| 500 | 服务自己没处理好 |
| 502 / 503 / 504 | 网关连不上上游、上游不可用、网关等上游超时 |

超时从外往里缩短。客户端 3 秒，网关 2.5 秒，服务调下游 2 秒。外层更长，内层的错误才能返回。网关先断开时，服务可能还在写数据库，调用方重试就会重复下单。所以写请求带幂等键，超时之后先查结果。

Spring 6.1 的 `RestClient` 和 Spring Cloud OpenFeign 按进程持有一份，关闭时跟着上下文走。每次请求新建客户端会反复握手。超时拆开：连接超时短，读超时按这个接口的正常耗时加余量，从池里借连接的等待单独设。

```java
RestClient client = RestClient.builder()
    .requestFactory(factoryWith(Duration.ofMillis(200), Duration.ofSeconds(2)))
    .build();
```

重试只针对幂等的 GET，以及带了幂等键的 POST。没有幂等键的下单，超时后先查结果。重试要有次数上限和抖动，否则一个慢下游会让所有调用方同步放大流量。

入口在 TLS 终止后把 `X-Forwarded-For` 和 `X-Forwarded-Proto` 传进来。这两个头只信任直接相连的代理写进来的值。应用看到的对端地址是网关的地址，真实客户端地址在代理追加的头里。应用如果直接听公网，就不能用客户端自己带的转发头做访问控制。Spring 的 `ForwardedHeaderFilter` 只在信任边界后面打开。

### TLS

TLS 1.3 握手是 1-RTT：客户端发支持的参数和密钥份额，服务端回证书和自己的密钥份额，两边算出会话密钥。1.2 通常要 2-RTT。恢复会话时 1.3 可以 0-RTT，重放风险和 HTTP/3 的 0-RTT 一样。证书在入口上，JVM 里不再做握手。轮换证书是入口的配置，不用重新发布应用镜像。服务之间如果要 mTLS，由网格或网关终结，应用仍按 HTTP 处理身份头。

### 负载均衡

四层负载均衡按 IP 和端口转发，不看 HTTP。云上的网络型负载均衡、Kubernetes 的 Service 和 kube-proxy 都是这一层。一条连接被转到某个后端之后就留在那里，直到连接结束。

七层负载均衡看 Host、路径和头。Ingress、Nginx、Spring Cloud Gateway 是这一层。它可以按请求做路由、限流和 TLS。HTTP 长连接上的多个请求可以到不同后端，前提是代理在请求边界上转发。WebSocket 和 gRPC 流是一条长连接，会被钉在选中的那个后端上。

均衡算法：轮询适合实例能力相近的无状态服务。最少连接适合请求耗时差很多的场景。一致性哈希按某个键把同一类请求送到同一台，缓存节点或有本地状态时用。权重用来把新版本慢慢放进来。Spring Cloud LoadBalancer 默认在实例列表上轮询，列表来自 DiscoveryClient，并有一份短缓存。

健康检查失败才摘除。检查要打应用的就绪接口，只探 TCP 端口会把还没加载完的进程放进轮询。Spring Boot 在 Kubernetes 里会自动打开 `/actuator/health/liveness` 和 `/actuator/health/readiness`。就绪组默认只包含应用是否启动完成，不包含数据库。要把数据库放进就绪，得显式加进健康组。这一段的探针细节在容器那一节。

### gRPC

gRPC 跑在 HTTP/2 上。接口和字段写在 protobuf 里，`protoc` 生成 Java 的请求类和 stub。一条 RPC 是 HTTP/2 上的一个流，消息按长度前缀放进 DATA 帧。字段号是合同的一部分：加字段用新的号，旧客户端忽略不认识的号；已经发出去的号不改含义、不复用。

四种调用。一元是一个请求一个响应。服务端流是一个请求之后持续回多条。客户端流是连续上传再收一个汇总。双向流是两边随时写。对外的 HTTP API 仍用 JSON。内部、字段稳定、或者要流式的调用用 gRPC。

通道按进程建一条，stub 从通道生成。`ManagedChannel` 的关闭跟在应用停机里做。截止时间用 `withDeadlineAfter`，写进 `grpc-timeout`，沿调用链往下传。每一跳看剩余时间，不够就返回 `DEADLINE_EXCEEDED`，不再往下调用。调用方没设截止时间时，服务端给一个自己的上限。

```java
ManagedChannel channel = ManagedChannelBuilder.forAddress("order", 50051)
    .usePlaintext()
    .build();
OrderGrpc.OrderBlockingStub stub = OrderGrpc.newBlockingStub(channel)
    .withDeadlineAfter(1, TimeUnit.SECONDS);
```

阻塞 stub 会占住调用线程。挂在虚拟线程上可以，挂在 Netty EventLoop 上不行。异步用 `newStub` 的 `StreamObserver`。

状态码和 HTTP 的不是同一套。`OK` 成功。`CANCELLED` 是调用方取消。`DEADLINE_EXCEEDED` 是截止时间到了。`NOT_FOUND`、`ALREADY_EXISTS`、`FAILED_PRECONDITION` 是业务前置条件。`RESOURCE_EXHAUSTED` 是配额或限流。`UNAVAILABLE` 是暂时连不上，只对幂等的方法重试。`INTERNAL` 是服务端没处理好。

元数据用来传认证和 `traceparent`。默认单条消息大约 4MB，大文件走对象存储，RPC 里只传地址。

负载均衡要按 RPC 而不是按连接。一条 HTTP/2 连接上可以跑很多 RPC。四层负载均衡选定后端之后，这条连接上的全部 RPC 都进同一个 Pod。无头 Service 把 Pod 地址交给客户端，客户端用 `round_robin`；或者用能识别 HTTP/2 流的代理按 RPC 转发。默认的 `pick_first` 只会盯住解析结果里的第一台。

空闲连接会被中间设备掐掉。心跳要比这条空闲时间更勤，同时不要短于服务端允许的最小 ping 间隔，过勤会被 `GOAWAY`。

### WebSocket 与 SSE

这两条都是服务器持续往浏览器推数据。差别在方向和连接怎么活。

WebSocket 从一条 HTTP 请求升级，成功是 101，之后双方都可以发帧：文本、二进制、ping、pong、关闭。它适合聊天、协同编辑这种两边都要主动写的场景。认证放在升级请求的 Cookie 或 `Authorization` 里。令牌放在查询串里会进访问日志。Spring 用 `WebSocketHandler`，`afterConnectionEstablished` 里登记这条会话，`handleTextMessage` 里处理帧，`afterConnectionClosed` 里摘掉。

SSE 仍是一条 HTTP 响应，`Content-Type` 是 `text/event-stream`。服务器往下写事件，浏览器不能在这条响应上反向写业务数据。客户端要发命令就另开 POST。它适合通知、任务进度、模型逐字输出。Spring MVC 用 `SseEmitter`，超时设成 0 表示由容器的空闲时间来管，发送用 `event().id(id).data(body)`。

一条事件以空行结束。`id` 是续传位置，`event` 是类型，`data` 是正文。浏览器的 `EventSource` 只会发 GET，会带 Cookie，不会带 `Authorization`。断线后它自动重连，并带上 `Last-Event-ID`。Nginx 默认会缓冲响应，代理侧关掉缓冲，事件才能立刻到客户端。

一条 SSE 在 HTTP/1.1 上占住浏览器到这个域名的一个连接名额。HTTP/2 上它只是一个流。WebSocket 升级之后不再受这个名额限制，但网关要允许升级。

多副本时，连接落在哪一台，消息就要能送到那一台。每台进程把自己接住的会话放在内存里。要广播时，先发到 Redis，各进程再写自己的会话。Pub/Sub 不留消息，断线期间的内容要靠 Redis Stream 或数据库按 id 补。

心跳间隔短于负载均衡和网关的空闲断开时间。WebSocket 用 ping，SSE 用注释行或一条心跳事件。滚动更新会把这条长连接断开，客户端重连到新后端，而不是每个请求平稳地切过去。

---

## 4. 操作系统（Linux）与 JVM

Java 进程跑在这套机制上：虚拟内存、页缓存、调度、epoll、信号、cgroup。容器是同一套机制划出来的视图。JVM 在上面再加一层自己的线程、内存和安全点。

### 进程、线程与虚拟线程

进程有独立的地址空间、文件描述符表和 PID。线程共享地址空间，各自有栈和寄存器。创建成本从高到低是进程、平台线程、虚拟线程。隔离性反过来。

Java 21 之前，HotSpot 把每个 `java.lang.Thread` 映射成一条操作系统线程。阻塞在 `read`、锁或 `sleep` 上时，这条操作系统线程一起停。请求一多，线程栈（`-Xss` 默认大约 1MB）和上下文切换先成为成本。

Java 21 的虚拟线程（JEP 444）由 JVM 调度，挂在少量载体平台线程上。阻塞在 I/O、`park`、`ReentrantLock` 时，虚拟线程可以卸载，载体去跑别的虚拟线程。JDK 21 到 23，虚拟线程进入 `synchronized` 或执行 native 方法时会钉在载体上，载体被占住。JDK 24 的 JEP 491 让 `synchronized` 里也能卸载。native 方法和外部函数仍会钉住。Spring Boot 3.5 的文档因此建议虚拟线程跑在 Java 24 或更新的版本上。钉住不会让结果算错，会让大量虚拟线程退化成少量平台线程。

```yaml
spring:
  threads:
    virtual:
      enabled: true
  main:
    keep-alive: true
```

打开之后，Tomcat 用虚拟线程处理请求，`server.tomcat.threads.max` 不再决定并发。并发改由 `max-connections`、Hikari 的池、下游限流和 Redis 的慢命令决定。虚拟线程是守护线程。如果进程里只剩定时任务这种虚拟线程，JVM 会退出。`spring.main.keep-alive=true` 用来保住这种进程。Web 容器自己的接受线程通常已经能让进程留着。

不要把虚拟线程放进一个固定大小的平台线程池里排队，那就回到了平台线程的并发上限。CPU 密集的计算仍用有界的平台线程池，并行度按核数。请求线程上做压缩、加密、大循环，会占住载体。

`ForkJoinPool.commonPool` 和许多并行流的并行度来自 `Runtime.availableProcessors()`。容器的 CPU limit 会被折成这个数字，而且经常向上取整。limit 很小而 GC 线程、公共池按这个数字开，会把配额打满，进程被节流。用 `-XX:ActiveProcessorCount` 把 JVM 看到的核数收住。

线程状态要分两层。JVM 里是 `NEW`、`RUNNABLE`、`BLOCKED`（等监视器）、`WAITING`、`TIMED_WAITING`、`TERMINATED`。操作系统里 R 是可运行，S 是可中断睡眠，D 是不可中断睡眠（通常在等磁盘），Z 是僵尸。`jstack` 里的 `BLOCKED` 是在等 Java 监视器，`WAITING` 里的 `parking` 多半是在等 AQS 或线程池队列。load average 是可运行和不可中断睡眠的进程数的滑动平均，不等于 CPU 使用率。IO 堵住时 load 很高，CPU 可以很低。

### 可见性与锁

JMM 用 happens-before 规定什么时候一个线程的写入对另一个线程可见。同一线程里的程序顺序、监视器的解锁对随后加锁、`volatile` 写对随后的读、`start` 之前的写入对新建线程、`join` 返回之后对已退出线程的写入，这些关系都可以传递。没有这层关系的普通读写，另一个线程可以一直看见旧值，编译器和 CPU 也可以重排。

`volatile` 保证可见性和有序性。复合动作 `i++` 仍然会丢更新。64 位 HotSpot 上普通 `long` 的一次写通常已经是原子的；`volatile` 补的是跨线程能看见这次写。原子自增用 `AtomicLong`，或者在更高竞争下用 `LongAdder` 把计数拆到多个 cell，读的时候再加总。`LongAdder` 适合统计，不适合要把当前值当作下一个版本号的场景。

`synchronized` 在当代 JDK 里是轻量级锁加重锁。无竞争时用 CAS 把对象头指向栈上的锁记录。有竞争并且自旋等不到，就膨胀成对象监视器，线程进入内核态的等待队列。偏向锁在 JDK 15 默认关闭，后续版本把实现删掉了，撤销偏向所需的安全点在现代机器上不划算。监视器是 MESA 模型：一个入口集做互斥，一个等待集做 `wait` / `notify`。`wait` 必须发生在已经持有监视器的时候，被唤醒后要重新检查条件，用 `while` 包住。

`ReentrantLock` 在 Java 层用 AQS 做同样的互斥。AQS 用一个 `state` 和一条 CLH 队列，获取失败就 `park`，释放时 `unpark` 后继。独占模式是锁，共享模式是信号量、倒计时门闩和读写锁里的读。非公平锁新来的线程先 CAS 一次，失败再入队。公平锁先看队列里有没有人。可中断、可超时、多个 `Condition`，这三件事 `synchronized` 做不到。`tryLock` 拿不到就走降级，避免请求线程无限等。

读写锁把读和写拆开。多个读可以同时持有，写互斥。读多写少、临界区又比一次哈希查找长时才划算。`StampedLock` 的乐观读不加锁，读完用戳校验，中间有写就升级成悲观读。它不可重入，虚拟线程和一般业务代码里 `ReentrantReadWriteLock` 更不容易用错。

`Semaphore` 做舱壁：同时只放 n 个调用进入同一个下游。`CountDownLatch` 等一组任务做完。`CyclicBarrier` 等一组线程到齐再一起放行。请求路径上更常见的是信号量，门闩和栅栏多在离线并行里。

线程没有安全打断点。`interrupt` 只是把标志设上，并唤醒睡在 `sleep`、`wait`、`park` 上的线程。对方要在循环里看 `Thread.currentThread().isInterrupted()`，或者让阻塞调用抛 `InterruptedException`。捕获之后如果还要继续退出，把中断标志设回去。线程池里的任务吞掉中断，池就关不干净。

`ConcurrentHashMap` 在 JDK 8 里是一张桶数组，桶里是链表或红黑树。空桶用 CAS 放进头节点。桶上已经有节点时，锁住这个桶的头节点，只串行化这一个桶上的写入。`get` 不再加锁，靠节点上的 `volatile` 读到新值。链表长度达到 8 并且数组长度达到 64 时，这个桶变成红黑树；数组还小就先扩容。计数拆成 `baseCount` 加若干 `CounterCell`，和 `LongAdder` 同一思路，`mappingCount` 是近似值。key 和 value 都不能是 null。JDK 7 是默认 16 个 `Segment`，每个 Segment 一把锁，锁的粒度比一个桶粗。

`ThreadLocal` 的值存在每个线程自己的 `ThreadLocalMap` 里。`Entry` 的 key 是对 `ThreadLocal` 的弱引用，value 是强引用。`ThreadLocal` 对象没有别的强引用时，下一次 GC 会把 key 清掉，value 还挂在线程的 map 上，变成 key 为 null 的脏 entry。之后的 `get`、`set` 会顺手清掉一部分脏 entry，时机不由业务控制。线程池里的线程不退出，value 会一直留到有人清理。`static` 的 `ThreadLocal` 被类强引用，key 不会被清掉，不 `remove` 的后果是下一个任务读到上一个请求的值。两种情况都在 `finally` 里 `remove`。虚拟线程数量可以很大，每个虚拟线程一份 map 会把堆顶上去。Java 25 的 `ScopedValue` 是不可变的、有作用域的值，方法结束就解除绑定，适合请求 id 这种只往下传的数据。Spring 的事务同步、`RequestContextHolder`、MDC 仍然是 `ThreadLocal`。把任务提交到另一个池子时，MDC 不会跟着走，要用 `TaskDecorator` 把 MDC 拷进工作线程，执行完再清掉。

### 线程池

`ThreadPoolExecutor` 的规则是：工作线程数小于核心数就新建线程；否则把任务放进队列；队列满了并且线程数小于最大数，再新建线程；还放不下就拒绝。无界的 `LinkedBlockingQueue` 永远不会满，最大线程数没有机会生效。

| 工厂方法 | 实际形状 | 后果 |
| --- | --- | --- |
| `newFixedThreadPool` | 固定线程，无界队列 | 任务堆积把堆撑满 |
| `newCachedThreadPool` | 线程数几乎不封顶，`SynchronousQueue` | 任务一慢就创建大量平台线程 |
| `newSingleThreadExecutor` | 单线程，无界队列 | 顺序执行，队列仍能撑满内存 |

生产用有界队列，明确核心数、最大数、存活时间和拒绝策略。默认的 `AbortPolicy` 抛 `RejectedExecutionException`，调用方可以失败或降级。`CallerRunsPolicy` 把任务退回调用线程，形成反压；调用线程如果是 Netty EventLoop，就会把整个事件循环堵住，这种地方改成有界拒绝。

队列自己的约定要和池的策略分开看。`add` 在满的时候抛异常，`offer` 返回 false。`remove` 在空的时候抛异常，`poll` 返回 null，`take` 会堵住直到有元素。`ArrayBlockingQueue` 是有界数组，入队出队共用一把锁。`LinkedBlockingQueue` 不写容量时是无界的，读写可以各用一把锁，池如果用它，最大线程数就没有机会生效。`SynchronousQueue` 没有缓冲，放入的线程必须等到另一个线程把这个元素取走。缓存线程池用它来做到「来一个任务就尽量开一个线程」。请求路径上的池用有界的 `ArrayBlockingQueue`。

```java
ThreadPoolExecutor pool = new ThreadPoolExecutor(
    8, 8, 0L, TimeUnit.MILLISECONDS,
    new ArrayBlockingQueue<>(1000),
    new ThreadFactoryBuilder().setNameFormat("order-%d").build(),
    new ThreadPoolExecutor.AbortPolicy());
```

CPU 密集的池，线程数按核数。IO 密集并且仍用平台线程时，线程数可以大于核数，上限是下游连接池能承受的并发，再多只是在池里排队。队列长度按「平均处理时间 × 可接受的排队延迟」估。拒绝次数做成指标。

Tomcat 自己的 `TaskQueue` 改了 `offer` 的行为：当前线程都忙、线程数还没到 `maxThreads` 时，`offer` 返回 false，迫使池先把线程扩到 `maxThreads`，然后再把请求放进队列。JDK 标准池是先填队列再扩到最大线程。所以「Tomcat 线程数一直涨到 max、队列却很空」是它的策略，不是泄漏。打开虚拟线程之后，这条策略让位给虚拟线程调度。

停机用 `shutdown` 拒绝新任务，再 `awaitTermination` 等队列里的任务做完。`shutdownNow` 会 `interrupt` 正在跑的任务并返回还没开始的列表。`terminationGracePeriodSeconds` 要盖住这段等待。

### 类加载

加载一个类的顺序是：加载字节码、链接（验证、准备、解析）、初始化（执行 `<clinit>`）。初始化发生在第一次 `new`、第一次读静态字段、第一次调用静态方法，或者被反射主动触发。父类先于子类初始化。

双亲委派指的是每个类加载器只有一个 `parent`，`loadClass` 先交给 parent，parent 加载不了再自己 `findClass`。习惯叫双亲，结构上是一条父指针。启动类加载器加载 `java.base`，平台类加载器和应用程序类加载器往下排。Spring Boot 的可执行 jar 再挂一个 `LaunchedURLClassLoader` 去读 `BOOT-INF`。同一份类由同一个加载器加载，`java.lang.String` 就不会被应用目录里的同名类换掉。

自定义加载器覆写 `findClass`。覆写 `loadClass` 会把父委派拆掉。有两类场景故意不走这条默认路径。JDBC 的接口在平台加载器里，MySQL 驱动在应用加载器里，父加载器看不见子加载器的 jar，`ServiceLoader` 用线程上下文类加载器去加载驱动。Tomcat 的 WebappClassLoader 对 `WEB-INF` 里的类先查自己，让两个应用带不同版本的同一个库。Java 标准库仍然交给 parent。

类卸载发生在加载它的类加载器不再被引用之后。元空间涨、又降不下来，先看是不是在循环里 `new` 新的类加载器或动态生成类，而不是先把堆加大。

### 内存

每个进程有自己的虚拟地址空间。页表把虚拟页映射到物理页。缺页时内核分配物理页或从磁盘把文件页读进来。`free` 里的 available 才是还能给应用用的内存，buff/cache 里的页缓存可以回收。

页缓存是文件读写的中间层。`write` 返回只说明进了页缓存，`fsync` 才把脏页交给磁盘。数据库的提交要 fsync 日志。容器的可写层不适合放要保留的数据。

脏页由 `dirty_ratio` 和相关阈值控制。写得太猛时，进程会在 `balance_dirty_pages` 里停住。这和 InnoDB 刷脏页是同一类现象，一层在内核，一层在缓冲池。

JVM 的内存不止堆：

| 区域 | 谁在用 | 容器里怎么留 |
| --- | --- | --- |
| 堆 | 对象。JDK 9 起默认收集器是 G1 | `MaxRAMPercentage` 留出非堆 |
| 元空间 | 类元数据，在本地内存 | 类加载泄漏会涨到 `MaxMetaspaceSize` |
| 线程栈 | 每条平台线程一份，约 1MB | 线程数 × 栈 |
| 直接内存 | NIO `DirectByteBuffer`、Netty | 默认上限约等于堆上限，单独设 `MaxDirectMemorySize` |
| 代码缓存 | JIT 编译后的代码 | 一般几十到一两百 MB |

回收从 GC Roots 出发，标记还能走到的对象。走不到的对象才是垃圾。Roots 包括：虚拟机栈和本地方法栈里的引用、静态字段、JNI 全局引用、被 `synchronized` 持有的监视器对象。一次 Full GC 之后老年代还在涨，先查 Roots 上挂着的大对象（静态集合、线程池里的任务、未关闭的类加载器），再谈换收集器。

引用有四种强度。强引用是普通赋值，有它就回收不了。软引用要到内存紧张时才被回收，适合可重建的缓存。弱引用在下一次 GC 就回收，只要没有更强的引用还指着它；`ThreadLocalMap.Entry` 的 key、`WeakHashMap` 的 key 都是弱引用。虚引用拿不到对象本身，对象被回收时通过引用队列得到通知，直接内存的 `Cleaner` 走这条路径。

G1 把堆分成区，年轻代回收走复制，老年代用混合回收。超过区一半的对象当大对象直接进老年代。停顿目标用 `-XX:MaxGCPauseMillis`，默认 200 毫秒。分代 ZGC 从 JDK 21 可用，JDK 23 起选 ZGC 时默认就是分代，停顿通常在亚毫秒到几毫秒，代价是吞吐和内存开销。默认收集器仍然是 G1。先看停顿和分配速率，再决定要不要换收集器。

容器里 `-XX:+UseContainerSupport` 默认开启，JVM 把 cgroup 的内存上限当作物理内存。没写 `-Xmx` 时，`MaxRAMPercentage` 默认 25，2Gi 的 limit 只有大约 512Mi 的堆，其余堆增长发生在第一次流量到来时。`InitialRAMPercentage` 默认很低，堆会在运行中扩张。把两者设成同一个值，启动时就把堆稳定下来：

```text
java -XX:InitialRAMPercentage=70.0 -XX:MaxRAMPercentage=70.0 -XX:+ExitOnOutOfMemoryError -jar app.jar
```

`-XX:+HeapDumpOnOutOfMemoryError` 能留下现场，dump 要写到卷上，大堆的 dump 会拖慢退出、写满磁盘。`-XX:+ExitOnOutOfMemoryError` 让堆 OOM 时进程退出，kubelet 才能按重启策略拉起。没有这个开关时，一次堆 OOM 往往只是那条线程抛出 `OutOfMemoryError`，进程还在，存活探针仍然成功，服务已经在丢请求。

cgroup 的内存用尽由内核杀进程，退出码 137，Java 异常来不及抛。`dmesg` 或 Pod 状态里是 `OOMKilled`。直接内存耗尽的信息是 `Direct buffer memory`，元空间耗尽会写明 Metaspace。三种要对着不同的区域加容量或收泄漏，不能只加大堆。

交换分区会把匿名页换出内存。数据库、Redis 和 JVM 的机器关掉交换。`vm.swappiness` 调低只是减少换出倾向。容器节点同样关掉交换，否则内存限制和驱逐的判断会被交换搅乱。

JDK 默认不会把空出来的堆立刻还给操作系统。G1 会在空闲时归还一部分，RSS 仍然经常停在高位。按内存使用率做自动扩容时，阈值要按稳定后的 RSS 设，并且不要用「堆已用 / 堆上限」当扩容信号，那条曲线在 Full GC 之后也不会回到零。

### 磁盘、IO 与零拷贝

一次 IO 有五种历史模型。阻塞 IO：线程停在 read 上直到数据到。非阻塞 IO：没有数据就立刻返回。IO 复用：用 epoll 先等哪些 fd 就绪，再去读。信号驱动：就绪时发信号。异步 IO：内核完成后通知应用，Linux 上现在用的是 io_uring。

Java NIO 的 `Selector` 在 Linux 上是 epoll，默认水平触发：缓冲区里还有数据，下一次 `select` 仍会返回。Netty 的本地 epoll 传输可以用边沿触发，必须一直读到 `EAGAIN`。`AsynchronousSocketChannel` 这条 AIO 路径在 Linux 上没有成为 Netty 和 Tomcat 的选择。生产网络 IO 是 NIO：Tomcat 的 Poller、Netty 的 EventLoop。磁盘读写放进线程池，或者用支持 io_uring 的库。请求线程上对大文件做阻塞读写，会把平台线程或载体占住。

epoll 在 fd 很多时用事件回调，不用像 select 那样每次拷贝整张 fd 集合。Tomcat 和 Netty 都是一个或少数几个线程跑 selector，工作线程只处理已经就绪的请求。在 EventLoop 里做 JDBC 或 `Thread.sleep`，这条循环上的所有连接一起停。

零拷贝分两条。`FileChannel.transferTo` 走 `sendfile`，内核在页缓存和套接字之间拷贝，数据不进用户缓冲区。Kafka 把日志段发给消费者、Nginx 把静态文件发给客户端，走的是这条。`mmap` 把文件映射进地址空间，适合按页随机读，数据仍要从内核拷到套接字。应用自己 `read` 进堆再 `write` 出去，是两次多余的拷贝。大文件下载交给网关或对象存储，应用只签发地址。

inode 是文件的元数据，目录项把名字指到 inode。删除打开着的文件只是拿掉目录项，进程还握着 fd 时空间不会释放。日志切割之后，进程如果继续写已经删除的 fd，磁盘还在涨，`ls` 却看不到文件。Java 服务把日志打到 stdout，由容器运行时收集，避免自己管滚动和 fd。

文件描述符上限在 `/proc/<pid>/limits` 里。每个连接、每个打开的文件各占一个。`Too many open files` 先看是连接泄漏还是上限太低。Hikari、Lettuce、HttpClient 各有一份连接，泄漏时常表现为池在借新连接、旧连接没关。

### 信号和停机

`SIGTERM` 是请进程退出。JVM 收到后跑关闭钩子，Spring 在钩子里关闭上下文。`SIGKILL` 不能捕获。`SIGSTOP` 也不能捕获。容器停止先发 `SIGTERM`，宽限时间到了再 `SIGKILL`。

shell 形式的 `ENTRYPOINT java -jar app.jar` 会让 shell 成为 PID 1，信号打在 shell 上，JVM 收不到，最后被强杀，在途请求被掐断。用 exec 形式，让 `java` 成为 PID 1。需要在 PID 1 上收割僵尸时，用 `tini` 作为入口，再由它拉起 Java。

Spring Boot 3.4 起，嵌入式 Tomcat、Jetty、Reactor Netty、Undertow 默认优雅停机。上下文关闭的早期会停止接收新请求，已经进来的请求可以做完，时限是 `spring.lifecycle.timeout-per-shutdown-phase`，默认 30 秒。Tomcat 在网络层停止 accept。要回到旧行为，设 `server.shutdown=immediate`。3.3 及更早的默认是 immediate，要自己打开 graceful。IDE 停进程时如果发的是杀进程而不是 `SIGTERM`，看起来会像优雅停机没生效。

关闭顺序：先拒绝新请求，再等待已经进来的请求，然后关闭数据源、Redis、消息客户端，最后等待线程池。`@PreDestroy` 里关连接。钩子里只做通知，长时间的清理放在 Spring 的生命周期里，钩子有自己的时间预算。

### 看一台机器

| 现象 | 看什么 |
| --- | --- |
| CPU 打满还是在等 | `vmstat 1` 的 us、sy、id、wa、st。st 是被宿主机抢走的时间 |
| 哪个线程在用 | `top -H -p <pid>`，再把 tid 转成十六进制对 `jstack` |
| 堆和 GC | `jcmd <pid> GC.heap_info`，`jstat -gcutil`，JFR |
| 磁盘是否饱和 | `iostat -x 1` 的 await 和 `%util` |
| 连接堆在哪 | `ss -s`，`ss -tan` 按状态数 |
| 是不是被 OOM | Pod 的 `OOMKilled`，或 `dmesg` |
| 文件描述符 | `/proc/<pid>/limits` 和 `/proc/<pid>/fd` |
| 虚拟线程钉住 | JFR 里的 pinning 事件，或 `jcmd Thread.dump_to_file` |

`jcmd` 从进程内部工作，容器里一般不需要额外的 ptrace 权限。async-profiler 和 `perf` 需要更开的权限，排障时再加。`st` 持续大于 0，是 CPU 限额或邻居把时间抢走了。`wa` 高是在等磁盘。

### cgroup

cgroup 限制和统计一组进程的 CPU、内存、IO 和进程数。cgroup v2 是一棵统一的树。Kubernetes 的 requests 用来调度，limits 写成 cgroup 上限。CPU limit 是 `cpu.max` 里的配额，用完这个周期的配额，进程被节流。GC 和 JIT 需要突发的 CPU，配额太紧时停顿会变长，平均 CPU 可以看起来不高。被节流的时间在 `container_cpu_cfs_throttled_seconds_total` 里。内存 limit 是 `memory.max`，超了就 OOM。超内存是杀进程，超 CPU 是变慢。

namespace 划出进程能看见的资源：PID、挂载点、网络栈、主机名、用户、IPC。容器里的 PID 1 在宿主机上只是一个普通进程。Service 的 ClusterIP 是集群网络里的地址，由 kube-proxy 或 eBPF 转成 Pod IP。

---

## 5. 开发框架

框架负责把 HTTP 变成一次方法调用，并管好对象的创建、事务和生命周期。业务规则留在服务自己的模块里。这里以 Spring Boot 3 和 Spring MVC 为准。WebFlux 用于网关和长连接，JPA 用于管理端的简单模型。

### 容器怎么把应用拉起来

`SpringApplication.run` 创建环境，加载 `application.yaml`、环境变量和命令行，激活 profile，然后 `refresh` 应用上下文。

`refresh` 里和业务有关的顺序是：`BeanFactoryPostProcessor` 先改 Bean 定义，`ConfigurationClassPostProcessor` 解析 `@Configuration`、`@ComponentScan`、`@Import` 和自动配置；然后注册 `BeanPostProcessor`；最后预实例化单例。自动配置的清单在 `META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`。条件注解决定要不要生效：`@ConditionalOnClass` 看依赖在不在，`@ConditionalOnMissingBean` 看用户有没有自己声明。用户自己声明的 `DataSource` 会让 Boot 的数据源自动配置退开。

`@SpringBootApplication` 是三件事：`@Configuration`、`@EnableAutoConfiguration`、`@ComponentScan`。扫描范围是启动类所在的包及其子包。启动类放在过窄的包里，别的模块的 `@Component` 不会进容器。

一个单例的生命周期：

1. 构造器注入。依赖在构造参数里声明，字段上的 `@Autowired` 是后手，测试和不可变对象都更差。
2. `BeanPostProcessor` 处理 `@Autowired`、`@Value`。
3. `Aware` 回调，例如拿到 `ApplicationContext`。
4. `@PostConstruct`，然后 `InitializingBean.afterPropertiesSet`。
5. `BeanPostProcessor` 的后置方法。AOP 代理经常在这一步包上去。
6. 使用。
7. 容器关闭时 `@PreDestroy`。

作用域默认单例，整个容器一份。`prototype` 每次取一次新建，不要把原型注入进单例的字段，那样只会在单例创建时拿一次。请求级状态放在方法参数、`ScopedValue` 或请求属性里，不放进单例的字段。

循环依赖在字段注入或 setter 注入的单例上才能解开。构造器循环依赖解不开，对象还没构造出来，没有引用可给对方。Spring Boot 2.6 起默认禁止循环依赖，`spring.main.allow-circular-references` 保持 false。下面这套缓存是允许环的时候容器在用的机制。

三个缓存分工不同：

| 缓存 | 放什么 |
| --- | --- |
| `singletonObjects` | 已经完成注入和初始化的单例 |
| `earlySingletonObjects` | 提前暴露过的对象。如果这个 Bean 需要 AOP，这里放的就是代理 |
| `singletonFactories` | 一个 `ObjectFactory`。被调用时才决定要不要做早期代理 |

A 的构造器已经执行完、属性还没填，容器先放一个工厂进去，再去创建 A 依赖的 B。B 反过来要 A 时，工厂调用 `getEarlyBeanReference`。这一步会跑 `SmartInstantiationAwareBeanPostProcessor`，需要代理就把代理放进 `earlySingletonObjects`，并把工厂删掉，保证只包装一次。B 拿到的是代理。A 稍后走正常的 `postProcessAfterInitialization` 时，发现自己已经被提前暴露过，最终注册进 `singletonObjects` 的必须是同一个对象。

只放两级、在构造完就把原始对象塞进缓存，B 会拿着没被代理的 A，事务和切面都不在这次调用上。工厂把「要不要代理」推迟到第一次被循环依赖用到的时候，提前暴露的对象和最终的单例才是同一个。没有循环依赖时，工厂不会被调用，代理仍在初始化之后的那一步创建。

之后如果又有一个 `BeanPostProcessor` 把 A 包成另一个对象，容器会启动失败：别的 Bean 已经注入了早期那个引用。这是拆环的信号，不是把 `allow-circular-references` 打开。

`BeanFactory` 是容器，`getBean` 从它里面拿对象。`FactoryBean` 是容器里的一个 Bean，负责生产另一个对象。`getBean("sqlSessionFactory")` 拿到的是 `getObject()` 的产物，`getBean("&sqlSessionFactory")` 才是 `FactoryBean` 自己。MyBatis 的 `SqlSessionFactoryBean`、每个 Mapper 接口对应的 `MapperFactoryBean` 都是这种生产方式：接口本身没有实现类，代理由工厂在 `getObject` 里造出来。

`@Autowired` 按类型装配。同一类型有多个 Bean 时，用 `@Primary` 或 `@Qualifier` 指定。`@Resource` 来自 JSR-250，默认把字段名当作 Bean 名字去找，找不到再按类型。`name` 写死了却没有同名 Bean，就直接失败，不再按类型兜底。构造器注入把依赖写在参数上，缺 Bean 在启动时就失败。

### 请求怎么走到方法

一条请求的路径是：Tomcat 读完 HTTP，过滤器链先走，然后进入 `DispatcherServlet`。`HandlerMapping` 按路径和方法找到处理器，`HandlerInterceptor.preHandle` 再走，`RequestMappingHandlerAdapter` 用参数解析器绑定路径、查询、头和正文，Bean Validation 在绑定之后校验，控制器方法运行，返回值处理器把对象写成 JSON，拦截器 `postHandle`，最后过滤器按相反顺序退出。

过滤器和拦截器的位置不同。过滤器是 Servlet 规范的，在 `DispatcherServlet` 之外，可以换掉请求和响应、可以不把请求交给 Spring。拦截器是 Spring 的，只有找到处理器之后才运行，能拿到 `HandlerMethod`。鉴权如果要挡住静态资源和健康检查以外的全部流量，放在过滤器里更靠外。只针对控制器的日志和权限，放拦截器。CORS 的过滤器放在靠外的位置，里面的错误响应才带得上跨域头。

`@ControllerAdvice` 按异常类型收口。业务异常带稳定的错误码，未捕获的异常统一成 500，日志里留栈，响应里不留栈。把内部异常的消息原样返回，会把 SQL 和路径暴露给调用方。校验失败是 400，正文里带字段位置。业务上的「这笔订单不属于这个用户」在方法里显式返回 403 或 404。

`@RequestBody` 的校验靠参数上的 `@Valid`。方法参数上的约束（`@RequestParam @Min(1)`）要在类上加 `@Validated` 才会生效。校验只证明形状合法，订单归属和状态机留在服务里。

配置用 `@ConfigurationProperties` 绑定，启动时校验一次，缺字段就起不来。`@Value` 散落在各处，缺了要到第一次用才发现。profile 用来切环境，业务开关进配置中心，不靠重新编译。

请求 id 从网关的头读进来，没有就自己生成，放进 MDC。日志格式里留 `%X{traceId}`。下游调用继续传这个头。trace 的 `traceparent` 由 Micrometer Tracing 或 OpenTelemetry 的注入器写，和业务自己的请求 id 可以是同一个值，也可以并存，日志里两个都打。

### 事务代理

`@Transactional` 由 Spring AOP 实现，默认是代理。Boot 里 `spring.aop.proxy-target-class` 默认为 true，优先用 CGLIB 代理类。调用必须从容器外面打到代理上，事务拦截器才会在方法前后开关事务。

下面这些情况里注解不会按你想的生效：

- 同一个类里的方法互相调用。`this.placeOrder()` 不经过代理。把事务方法挪到另一个 Bean，或者从 `ApplicationContext` 取代理再调。
- 方法不是 `public`。Spring 的事务属性源默认只看 public 方法。
- 类或方法是 `final`。CGLIB 生成不了子类覆盖。
- 对象不是容器创建的。`new OrderService()` 没有代理。
- 异常在方法内部被吃掉。代理看到的是正常返回，事务提交。
- 抛出的是受检异常。默认只在 `RuntimeException` 和 `Error` 上回滚。业务异常如果是受检的，要写 `rollbackFor = Exception.class`。
- 传播行为是 `NOT_SUPPORTED` 或 `SUPPORTS`，并且外面没有事务。方法里的 SQL 会自动提交。
- 多数据源时，注解绑的是其中一个事务管理器。另一个库的连接不在这个事务里。

传播行为决定要不要加入外层事务：

| 传播 | 行为 |
| --- | --- |
| `REQUIRED` | 有事务就加入，没有就新建。默认 |
| `REQUIRES_NEW` | 挂起外层，新建一个独立事务。内层提交对外层可见，外层回滚不会撤内层 |
| `NESTED` | 外层存在时建保存点。内层失败可以只滚到保存点 |
| `MANDATORY` | 必须已经在事务里，否则抛错 |
| `NEVER` | 必须没有事务 |

`REQUIRES_NEW` 会从池里再借一条连接。内层和外层再去锁同一批行，容易和自己死锁。它适合「操作日志必须留下，即便业务回滚」这种真正独立的写入。嵌套保存点仍是同一条连接、同一个数据库事务。

事务提交之后的动作放进 `TransactionSynchronization.afterCommit`，例如删缓存、通知一个内存里的监听器。`@TransactionalEventListener(phase = AFTER_COMMIT)` 是同一件事的注解形式。监听器如果再去发网络请求，失败了事务已经提交，要有补偿。不要在 `afterCommit` 里假设还能用刚才那条连接做新的写入，连接可能已经还回池。

只读事务 `readOnly = true` 让驱动声明只读，优化器可以少做一些写相关的准备。它不负责把语句送到副本。

JPA 的 `open-in-view` 在 Boot 里默认开着：整个请求期间占着一条连接，视图渲染时还能懒加载。JSON 接口把 `spring.jpa.open-in-view=false`，事务结束就还连接，懒加载在事务外直接失败，问题在测试里暴露。复杂 SQL 用 MyBatis 写在 Mapper 里。JPA 适合管理后台、字段和表差不多一一对应的模型。

### 和其他运行时的边界

Spring MVC 是一个请求一条线程（或一条虚拟线程），业务方法可以阻塞在 JDBC 上。Spring WebFlux 和 Gateway 跑在 Netty 上，少量 EventLoop 扛很多连接。在 Gateway 的过滤器里调用阻塞的 `RestTemplate` 或 JDBC，会把 EventLoop 占住，所有经过这条循环的请求一起等。网关里用 WebClient。业务服务保持 MVC。已经打开虚拟线程的 MVC 服务，用阻塞的 JDBC 和 RedisTemplate 是预期的写法。

`@Async` 把方法提交到另一个执行器，返回值是 `Future` 或 `CompletableFuture`。它同样依赖代理，自调用不生效。默认执行器在打开虚拟线程之后会换掉。队列仍要有界。必须做完的事放进消息队列，`@Async` 只用来做丢了也能接受的收尾。进程在任务跑完前被杀掉，这个任务就没了。

定时任务 `@Scheduled` 默认单线程执行器，上一个任务没完，下一个触发会被推迟。多个任务互相独立时指定调度器的池大小。集群里每个副本都会触发，必须加分布式锁、数据库唯一键，或者改成只在一个 Job 里跑。`fixedRate` 按开始时间算，任务超时会堆积；`fixedDelay` 按结束时间算。

### 测试和运行

接口测试用 `MockMvc` 或 `WebTestClient` 打控制器，不先起端口。`@WebMvcTest` 只装 MVC 这一层，依赖用 `@MockBean` 换成假的。数据库测试用 Testcontainers 起 MySQL，事务回滚让每个用例结束回到起点。Mapper 的 SQL 差异在集成测试里打真库，H2 的方言和 MySQL 对不齐的地方会漏。

本地用 Boot 的 devtools 或直接跑 main。生产关闭 devtools，镜像里不要带它。健康检查拆成存活和就绪，路径和 Kubernetes 探针那一节对上。访问日志打到 stdout。

---

## 6. 消息队列

队列用来解耦、异步和削峰。生产者写入后即可返回，消费者按自己的速度处理。下游停了，消息还在；队列自己停了，这条链路停。队列要单独做高可用。

本系统稍后执行、可以重试的任务可以用 Spring 的调度加上数据库任务表。要和别的服务交换业务事件，用 RabbitMQ、RocketMQ 或 Kafka，正文用 JSON。消息体里放业务键和版本，大文件放对象存储，消息里只放地址。

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

先插入幂等键，唯一键冲突就读出上一次的结果返回。同一个键配了不同的请求体，返回 409。订单表上的「用户 + 活动 + SKU」或请求 id 唯一键是同一件事的另一种落点。Redis `SET NX` 只能挡住短时间的重复。持久的去重放在数据库。

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

三种队列的差别：

| | RabbitMQ | RocketMQ | Kafka |
| --- | --- | --- | --- |
| 模型 | 交换机把消息推进队列，消费后从队列移除 | topic 下多个队列，Broker 存消息 | topic 下多个分区，追加日志，按保留时间删除 |
| 顺序 | 单个队列里、单个消费者时 | 同一个消息组进同一个队列 | 同一个 key 进同一个分区 |
| 堆积 | 队列变长，堆积很大时更适合让 Kafka 来接 | 可以堆积，受磁盘限制 | 按磁盘保留，适合大流量和回放 |
| 消费语义 | ack 之前可以重投 | 不可见时间之后重新可见 | 位点没提交就会再投 |

### RabbitMQ

生产者只对交换机发，消费者只对队列收。队列没绑定交换机就收不到消息。Spring AMQP 的监听器默认自动确认：方法正常返回就 ack，抛异常就拒绝。`defaultRequeueRejected` 为 true 时，失败的消息会立刻回到队列头，毒消息把消费者占满。把它关掉，让消息按死信策略走，或者用重试拦截器做有限次退避再进死信。

四种交换机。扇出复制到每一个绑定的队列。直连按完整的 routing key 匹配。主题用 `*` 匹配一个词、`#` 匹配零个或多个词。头交换机按消息头匹配。

publisher confirm 打开之后，Broker 接受消息才给回执。`spring.rabbitmq.publisher-confirm-type=correlated`，回调里按 correlation id 把发件箱那一行标成已发送。没开 confirm 时，`convertAndSend` 返回只说明写进了客户端缓冲区。队列不存在且设置了 mandatory 时，走 return 回调，发件箱那一行保持未发送。`publisher-returns` 要和 mandatory 一起开。

死信有三条来源：队列满了并且拒绝新消息、消费者拒绝且不重新入队、消息超过 TTL。队列参数 `x-dead-letter-exchange` 和 `x-dead-letter-routing-key` 指向下一跳。仲裁队列还可以设 `x-delivery-limit`，同一条消息被投递到这个次数就进死信。用「每条消息一个 TTL + 死信」做延迟时，队列按队头检查过期，队头没到期，后面已经到期的消息也出不去。延迟各不相同就用延迟插件，或按延迟档位拆队列。

队列用仲裁队列（quorum queue）。副本按 Raft 多数派确认。镜像队列是旧的做法。prefetch 是每个消费者一次拿多少条未 ack 的消息。设得太大，消息堆在一个慢进程里，别的进程空闲。`spring.rabbitmq.listener.simple.prefetch` 从 10 到 50 起，按处理时间调。一个队列上如果要求严格顺序，就只能有一个活跃消费者。

### RocketMQ

4.x 的部署里，NameServer 保存 Broker 路由，生产者先问 NameServer，再写到 Master Broker 上对应 topic 的队列。5.x 的新客户端走 gRPC，连 Proxy，由 Proxy 找 Broker。两套协议的语义相同，客户端不能混在同一个调用栈里假装互通。国内存量系统大量仍是 4.x 的 Remoting 客户端和 `rocketmq-spring-boot`。

Producer 往 topic 发，Consumer 按组拉。组里的消费者分摊消息，组内订阅必须一致。广播是组里每个消费者都收到全量。消息上有 topic、tag、keys、body。tag 用来在订阅时过滤，keys 用来按业务 id 查。

发送有同步、异步、单向。单向不等确认，丢了也不知道。同步和异步要等 Broker 的响应。刷盘和复制可以是同步或异步，同步刷盘加同步复制才接近「响应返回时多数副本已经有了」。默认常常是异步刷盘，Broker 宕机会丢最后一小段。

集群消费下，消息有一段时间不可见。处理完必须 ack。超过不可见时间还没 ack，消息重新可见。失败让它稍后重投，4.x 里次数到顶（默认 16 次）进入 `%DLQ%消费组`。死信要有人消费或告警。消费后的消息按保留时间删除，不是 ack 了就立刻从磁盘消失。

同一笔订单的创建、支付、取消用同一个消息组（sharding key），Broker 把它们放进同一个队列。顺序监听器对一个队列同时只跑一个线程。并发监听器不做这个保证。顺序只在一个队列里成立。扩容之后，新的 key 才会落到新队列，旧 key 的顺序不会因为加了消费者就并行。

事务消息：先发 half 消息，消费者看不见；执行本地事务；再提交或回滚。进程在提交前停了，Broker 回查 `checkLocalTransaction`。回查做成无副作用的查询：本地事务表里有记录就提交，没有就回滚。回查可能发生多次。4.x 的延迟消息是固定的十几个档位，5.x 可以指定任意延迟时间。需要「30 分钟后关闭未支付订单」时，档位对不上就用时间轮或数据库扫描，不把业务凑进最接近的档。

### Kafka

消息按 topic 再按 partition 追加，消费后不删，靠保留时间或大小清理。消费者属于一个组。同组一条消息只给一个消费者。另一组可以再消费全量。offset 提交了，同组就不会再投这条。分区可以回放，只要数据还在保留范围里。

Spring Kafka 把 `enable.auto.commit` 关掉，用容器的确认模式。默认 `AckMode.BATCH` 是这一批监听器都返回之后再提交。处理到一半进程停了，这一批会再投。`MANUAL` 要在代码里调用 `Acknowledgment.acknowledge()`，提交发生在监听器返回之后。`MANUAL_IMMEDIATE` 在调用当时就提交。自动提交等于拉到就算消费完，处理失败也会往前走。

```java
@KafkaListener(topics = "order", groupId = "inventory")
public void onOrder(ConsumerRecord<String, String> record, Acknowledgment ack) {
    inventoryService.apply(record.value());
    ack.acknowledge();
}
```

两次 `poll` 的间隔有上限 `max.poll.interval.ms`。处理太慢会被踢出组，分区分给别人。一次 poll 的条数 `max.poll.records` 按处理速度设。监听器里再同步调一个慢下游，很容易超过这个间隔。

新组从哪开始由 `auto.offset.reset` 决定。`latest` 只消费启动之后的新消息。`earliest` 从保留范围内最早的一条开始，之后仍接上次提交的位点。换 `group.id` 等于换一组新进度。

Rebalance 在组成员变化或分区数变化时发生。Range 按分区段切，RoundRobin 轮询，Sticky 尽量保住上次的分配。CooperativeSticky 是增量再均衡：只收回要挪走的分区，其余分区继续消费。Range 和 RoundRobin 会让全组先停下来再分配。默认的策略列表里 Range 在前。要增量再均衡，把 `partition.assignment.strategy` 设成 `CooperativeStickyAssignor`。协调器是该组 offset 所在 `__consumer_offsets` 分区的 leader。消费者先找到它，发 JoinGroup。协调器选第一个连上来的当组长，把成员列表交给它算分配方案，再通过 SyncGroup 把每人的分区发回去。再均衡期间这组成员暂停拉取。

消息 key 决定分区。同一订单号进同一分区，这个分区里才有顺序。key 太集中时，流量堆在一个分区上，加消费者也没用，因为一个分区同时只被组里的一个消费者读取。消费者数量超过分区数时，多出来的一直空闲。要提高并行度就先增加分区。分区数可以增加，减少会破坏 key 到分区的映射。

每个副本有自己的 LEO（log end offset），指向下一条要写入的位置，所以它是这条副本日志的末尾。HW（high watermark）是 leader 认为已经复制到全部 ISR 的偏移。消费者只能读到 HW 之前的消息。leader 的 LEO 已经往前走、消息也在 leader 的磁盘上，只要还有一个 ISR 的 LEO 没到这个位置，HW 就停在最慢的那个副本上，消费者读不到。跟随者的下一次 Fetch 才会把自己的进度告诉 leader，HW 的推进因此还要再等一轮拉取。这就是「副本盘上已经有了，消费端却看不到」。

生产者从 Kafka 3.0 起默认 `acks=all`、`enable.idempotence=true`。`acks=all` 要等消息进入当前全部 ISR，也就是 HW 跨过这条消息，生产者才收到成功。幂等生产者保证的是同一个生产者会话里重试不会写出重复序号。主题的 `min.insync.replicas` 至少为 2，副本因子至少 3。ISR 数量不够时，生产者收到 `NotEnoughReplicas`。`unclean.leader.election.enable` 保持关闭，避免一个落后的副本在没有 ISR 时被选成 leader，把 HW 以上的消息带丢。leader 切换时，新 leader 会把自己日志里超过前任 HW 的部分截掉，和已经承诺给消费者的范围对齐。消费者处理成功但提交位点失败，仍会再投一次。Spring 的 `DefaultErrorHandler` 在重试耗尽后默认记下错误并跳过这条，位点继续向前。业务消息要配 `DeadLetterPublishingRecoverer`，把失败的记录发到死信主题，而不是悄悄跳过。

积压看 lag：分区末端位点减去已提交位点。先分辨是处理变慢还是生产变快。扩容消费者之前先确认还有空闲分区。按 lag 扩容用 KEDA，HPA 的 CPU 反映不出堆积。

---

## 7. Docker 与 Kubernetes

容器把进程放进独立的 namespace，用 cgroup 限制资源，用镜像里的根文件系统启动。Kubernetes 在多台机器上调度这些容器，做自愈、滚动升级和服务发现。Java 服务要额外处理启动慢、堆外内存和信号。

### 镜像

镜像是只读层叠起来的。容器的写入落在最上面的可写层，容器删了可写层就没了。要留的数据用卷。层是按 Dockerfile 的每一条生成文件的指令缓存的。依赖很少变，代码经常变，所以先放依赖层，最后才放应用类。

基础镜像用 `eclipse-temurin:21-jre`，标签钉死小版本。JDK 24 以上跑虚拟线程时换成对应的 JRE。多阶段构建：第一阶段用 JDK 打包，第二阶段只留 JRE 和已经分层的内容。Spring Boot 的 jar 默认带 `layers.idx`，用 `java -Djarmode=layertools -jar app.jar extract` 拆成四层，变化少的放前面：

1. `dependencies`：正式版本的依赖。
2. `spring-boot-loader`：启动器。
3. `snapshot-dependencies`：快照依赖。
4. `application`：自己的类和资源。

改一行业务代码只让最后一层失效。3.2 起启动类是 `org.springframework.boot.loader.launch.JarLauncher`。3.1 及更早是 `org.springframework.boot.loader.JarLauncher`，写错包名容器会起不来。

```dockerfile
FROM eclipse-temurin:21-jre AS build
WORKDIR /src
COPY app.jar app.jar
RUN java -Djarmode=layertools -jar app.jar extract

FROM eclipse-temurin:21-jre
WORKDIR /app
RUN useradd --system --user-group --shell /usr/sbin/nologin app
COPY --from=build /src/dependencies/ ./
COPY --from=build /src/spring-boot-loader/ ./
COPY --from=build /src/snapshot-dependencies/ ./
COPY --from=build /src/application/ ./
USER app
EXPOSE 8080
ENTRYPOINT ["java", "-XX:InitialRAMPercentage=70.0", "-XX:MaxRAMPercentage=70.0", "-XX:+ExitOnOutOfMemoryError", "org.springframework.boot.loader.launch.JarLauncher"]
```

`USER` 放在入口前面，进程不要用 root。拷文件用 `COPY`。`ADD` 还会拉 URL、解压缩包，构建结果会跟着网络变，镜像里不用它。`EXPOSE` 只是文档，映射靠 `-p` 或 Service。exec 形式让 `java` 成为 PID 1，才能收到 `SIGTERM`。shell 形式会多一层 shell。`.dockerignore` 去掉 `.git`、`target`、本地 `.env`。镜像标签用 git 提交号。生产不用 `latest` 作为唯一标识。

日志打到 stdout。`/tmp` 在只读根文件系统上会让 Tomcat 解压失败，给 `/tmp` 挂一块 emptyDir。堆 dump 也写到这块盘上，避免写进可写层把节点磁盘打满。需要留存的上传文件进对象存储，不进容器磁盘。

Spring Boot 3.3 起可以用 CDS：训练跑一次，把类数据归档打进镜像，启动时 `-XX:SharedArchiveFile` 读它。启动探针的窗口能短一截。归档和 classpath 必须一致，依赖变了要重新训练。

Compose 把应用、MySQL、Redis、队列写在一份 YAML 里，给本地和测试用。服务之间用服务名当主机名。`depends_on` 只保证启动顺序。数据库写 `healthcheck`，应用用 `condition: service_healthy`。健康检查通过只说明端口开了，迁移是否跑完由应用自己重试。生产不用 `network_mode: host`，否则绕开 Service 的负载均衡。

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

配置进 ConfigMap，密钥进 Secret。密钥不写进镜像层，也不进 Git。只改 ConfigMap 不会重启容器，进程已经读进环境变量的值也不会变。需要整进程重载时，给 Pod 模板加一个配置摘要注解，摘要变了就滚动。`@ConfigurationProperties` 如果来自文件并且应用自己监听了变更，可以只重建客户端；数据源 URL 变了必须重建池，跟着滚动更稳妥。

### 持久卷

容器的可写层和 `emptyDir` 随 Pod 一起消失。`emptyDir` 适合 `/tmp` 和解压。`hostPath` 把节点上的目录露进容器，Pod 换一台机器就找不到原来的文件，也把节点文件系统暴露给进程。业务数据不放这两种。堆 dump 可以写到 emptyDir，避免写进可写层把节点磁盘打满。

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

磁盘快照是崩溃一致性，MySQL 可能正写着一页。库的备份仍用 `xtrabackup` 或带 GTID 的逻辑备份。非 root 用户要能写挂载点，Pod 的 `securityContext.fsGroup` 设成镜像里那个用户的组。

Java 服务自己无状态。上传文件进对象存储。PVC 留给自建的数据库、队列和必须落在磁盘上的大文件。托管的 RDS 和 Redis 不需要在业务 Pod 上挂盘。

清单的一级字段是 `apiVersion`、`kind`、`metadata`、`spec`、`status`。Deployment 的 `selector` 和 Pod 模板标签一致。`status` 由控制面写。

### 探针、资源和发布

JVM 加载类、Hikari 建连、本地缓存预热要几十秒。`startupProbe` 给足失败次数，过了启动阶段才开始存活探针。就绪探针失败只是从 Service 摘掉，不重启。存活探针打 `/actuator/health/liveness`，只判断进程还能不能恢复。就绪探针打 `/actuator/health/readiness`。

Spring Boot 在 Kubernetes 环境里会自动打开这两组探针。存活组默认只有存活状态，就绪组默认只有就绪状态，数据库、Redis 都不在里面。数据源抖动如果被放进存活组，kubelet 会把 Pod 全部重启。需要「库不通就不接流量」时，只把 `db` 加进就绪组：

```yaml
management:
  endpoint:
    health:
      group:
        readiness:
          include: readinessState,db
```

管理端口和业务端口分开时，探针可能打在一个仍然健康的管理端口上，业务端口已经接不了连接。`management.endpoint.health.probes.add-additional-paths=true` 会在业务端口暴露 `/livez` 和 `/readyz`。探针打业务端口。

```yaml
startupProbe:
  httpGet: { path: /actuator/health/liveness, port: 8080 }
  periodSeconds: 5
  failureThreshold: 30
readinessProbe:
  httpGet: { path: /actuator/health/readiness, port: 8080 }
  periodSeconds: 5
livenessProbe:
  httpGet: { path: /actuator/health/liveness, port: 8080 }
  periodSeconds: 10
```

内存按堆加上元空间、线程栈、直接内存和代码缓存估算，写进 `resources.requests` 和 `limits`。`MaxRAMPercentage=70` 时，limit 的大约三成留给非堆。200 条平台线程 × 1MB 栈就是 200MB。打开虚拟线程之后，载体线程接近核数，栈的总量小很多，直接内存和堆仍然要算。CPU 的 limit 会触发节流，G1 的停顿会变长。IO 型的 Java 服务把 CPU limit 设得明显高于 request，或者只设 request、用命名空间的 LimitRange 兜住上限。内存 limit 仍然要设。

QoS 按 requests 和 limits 的关系分：两者相等是 Guaranteed，只设了 requests 或两者不等是 Burstable，都没设是 BestEffort。节点内存紧张时，BestEffort 先被杀，然后是超出 requests 的 Burstable。

HPA 看 CPU 时，进程经常在等 JDBC，CPU 很低但连接池已经满了。更有用的是正在处理的请求数、Tomcat 忙碌线程、Hikari 活跃连接和等待线程。这些做成自定义指标再交给 HPA。队列堆积用 KEDA 按 lag 扩消费者。堆使用率不适合当扩容信号，GC 之后已提交的堆也不会回到零。

滚动更新用 `maxUnavailable: 0`、`maxSurge: 1`：先起一个新 Pod，就绪之后再停一个旧的。`minReadySeconds` 设几秒，避免刚通过探针就接全量流量。`PodDisruptionBudget` 的 `minAvailable` 保证腾挪节点时至少留一个能服务的 Pod。副本数是 1 时，PDB 和自愿驱逐会互相卡住，至少两个副本再加 PDB。

Pod 进入 Terminating 之后，端点摘除和 `preStop` 并行发生。摘除要等 kube-proxy 或 eBPF 刷新，这段时间新请求还会进来。`preStop` 里先等几秒，再让 JVM 处理 `SIGTERM`。`terminationGracePeriodSeconds` 从停机流程开始算，到点还没退出就 `SIGKILL`。preStop 的等待加上 `timeout-per-shutdown-phase` 要小于这个值。默认宽限 30 秒，preStop 睡 10 秒、优雅停机再要 30 秒，就会在事务还没结束时被强杀。把宽限调到 45 秒或更长。

```yaml
lifecycle:
  preStop:
    exec:
      command: ["sh", "-c", "sleep 10"]
terminationGracePeriodSeconds: 45
```

镜像拉取用 `imagePullPolicy: IfNotPresent` 配合不可变标签。拉取密钥放在 ServiceAccount 上。

---

## 8. 微服务

微服务是按业务能力拆开的一组进程：各自部署，各自拥有数据，通过网络调用或消息协作。拆开之后，故障面变小，发布可以分开。代价是一次业务操作跨过网络，事务、超时和观测都要单独设计。系统不够大时，一个仓库里的模块边界就够了。

### 边界

代码上对应三种边界：

- 一个进程里的包：`account`、`order` 只是目录，调用是方法调用。事务还是本地的。
- 独立进程：各自的镜像、Deployment、数据库。调用走 HTTP、Dubbo 或消息。一个进程停了，另一个进程还在。
- 内部包：发到内部仓库，各服务钉死主版本。包里放日志格式、追踪上下文、错误码。创建订单这种业务留在订单服务里。

按业务能力拆，不按技术层拆。把「所有数据库访问」放进一个服务、把「所有校验」放进另一个服务，一次请求仍然要穿过所有层，发布还得一起发。订单服务拥有订单表，商品服务拥有商品表。需要别人的数据时，要么同步调用拿一次，要么订阅对方的事件在本地留一份只读副本。两个服务写同一张表，就回到了共享数据库。

中台是把各业务线里重复的能力抽出来。业务中台是用户、会员、支付、权限这种各条线都要的服务。数据中台是统一的存储、建模和计算。技术中台是日志、配置、鉴权、任务这些共用平台。烟囱是每条产品线各做一份权限和订单。落地顺序是：先按业务拆开，再把共有能力收成平台，最后调整编制和投入。调用方拿用户 id 就能完成登录态校验，用户服务把存储细节留在自己这边。

拆分的信号是：这个模块要独立扩容，发布节奏和别人不同，故障时不想拖垮主路径，数据所有权已经清楚。反向的信号是：每次改需求都要改三个服务，同步调用链超过三跳，为了一次查询做分布式事务。这种情况下先把边界收回到一个进程里。

Java 进程之间有两条常用的调用方式。HTTP 加 JSON 用 Spring Cloud OpenFeign 或 `RestClient`，网关、外部伙伴和语言不同的调用方都走它。内部 Java 服务、调用频繁、要复用连接时用 Dubbo 3 的 Triple（基于 HTTP/2）。Dubbo 的应用级服务发现按应用注册，不再为每个接口拉一份地址列表。对外的 API 仍留在 HTTP。

### 同步调用上的保护

服务之间用一个进程级的客户端。超时拆开，重试只打在幂等请求上。超时从外往里缩短。

五个保护要一起用：

- 超时：每次下游调用都有上限，连接、读、从池里借连接分开。Feign 的 `connectTimeout` 和 `readTimeout` 按客户端分别设，默认值往往比网关还长。
- 重试：有次数、有抖动，只打幂等接口。OpenFeign 自己的 `Retryer.Default` 会重试多次；Spring Cloud OpenFeign 的自动配置把它换成了 `NEVER_RETRY`。LoadBalancer 在 classpath 里有 spring-retry 时还可以换实例再试。重试会放大流量。
- 熔断：连续失败或慢调用比例过高之后暂时不再调用。半开时放少量探测。打开期间返回缓存里的旧数据，或一个明确的稍后再试。
- 舱壁：每个下游一份信号量或一份连接限额。支付慢只占满支付的额度，查询还剩自己的连接。
- 限流：在入口按用户和接口收住，在服务里按自己的容量再收一层。

Sentinel 把这些落成规则。流控看 QPS 或并发线程数，直接拒绝、预热或匀速排队。熔断看慢调用比例、错误比例或错误数，时间窗之后放一个探测。热点参数限流按商品 id 收住某一个 SKU。系统规则看 load、CPU、平均 RT。`@SentinelResource` 圈住一个方法，阻塞时走 `blockHandler`，业务异常走 `fallback`。两者要分开：限流是保护，业务失败是另一种返回。Sentinel 的计数默认在进程内，每个副本各算各的。入口限流放在网关，效果对所有副本同时生效。集群限流要单独的 token server。

Resilience4j 是另一套实现，Spring Cloud CircuitBreaker 可以包住它。网关的 `CircuitBreaker` 过滤器、业务里的 Sentinel，只留一套。两套同时计数，阈值会对不上。

幂等键由调用方生成，放在头里，服务端用前面的幂等表记住结果。超时之后调用方用同一个键再请求，拿到的是第一次的响应。键要跟着业务单走，用户每点一次「提交」是一个新键，同一次提交的自动重试是同一个键。

```java
@FeignClient(name = "pay")
public interface PayClient {
    @PostMapping("/pay")
    PayResult pay(@RequestHeader("Idempotency-Key") String key, @RequestBody PayRequest body);
}
```

服务网格把 mTLS、部分重试和指标下沉到边车。应用仍然要设自己的超时和幂等。边车的重试和 Feign、LoadBalancer 的重试叠在一起，总次数是相乘的。开了网格之后，重试只留一处。

### 跨服务的数据

一次下单要改订单、库存和优惠券。这三个库没有同一个本地事务。做法按一致性要求选：

- 能放在一个库里的，就还是一个库、一个事务。拆服务不强制拆库。
- 发件箱加事件。订单事务提交后，库存服务消费事件再扣。中间有一段时间两边不一致，用对账补。
- Saga 是一串本地事务加补偿。编排式由一个协调者按步骤调用，失败了按相反顺序执行补偿。编舞式由每个服务发出事件，下一个服务自己接着做。补偿要做成幂等。Saga 没有隔离性：步骤之间别人能看见中间状态，业务上要用状态机把「支付中」「已取消」显式写出来。
- TCC 把每个资源分成 Try、Confirm、Cancel。Try 预留，Confirm 确认，Cancel 释放。隔离性比 Saga 好，三个阶段都要写。空回滚是 Cancel 比 Try 先到，Cancel 要能当成无事发生。悬挂是 Cancel 之后迟到的 Try，Try 要拒绝。每个阶段按事务 id 幂等。
- Seata 的 AT 模式在本地事务里记录前后镜像，写进同一库的 `undo_log`，提交时一起落盘。全局提交后异步删掉 undo。全局回滚时按 undo 补偿。TC 管全局锁和分支状态，TM 是 `@GlobalTransactional` 所在的进程，RM 是每个数据源上的代理。默认隔离下，别的事务能读到已经本地提交、全局还没完成的行。要挡住这种脏读，查询走 `SELECT FOR UPDATE` 去检查全局锁。AT 对 SQL 有限制，批量更新、多表和不支持的语句要改成 TCC 或不要放进全局事务。全局锁会把热点行的并发打下来。

两阶段提交让所有参与者先准备再提交。协调者在准备之后宕机，参与者会一直占着锁等它。跨服务的业务事务不用数据库的 XA 来包 HTTP。Seata AT 的两阶段是在应用层补镜像，和 InnoDB 内部 redo、binlog 的两阶段不是同一件事。

读自己的写入：写完去从库或去别人的只读副本，可能还没有这笔数据。用户刚提交的订单，查询打主库或打本地已提交的结果。列表和搜索可以接受秒级延迟。

接口演进用加字段、不改旧字段含义的方式。先让调用方能忽略新字段，再让调用方开始写新字段，最后才停用旧字段。同时在线的两个版本要都能处理对方的消息。消息体里带 schema 版本。Jackson 默认忽略未知字段的前提是没把 `FAIL_ON_UNKNOWN_PROPERTIES` 打开。破坏性变更用新的路径或新的 topic。

### 网关、配置与发现

鉴权、限流、黑名单放在入口，每个服务就不用各写一份。Spring Cloud Gateway 跑在 WebFlux 上：

- 路由：按谓词转到哪个上游。上游是负载均衡的服务名 `lb://order`，或一个固定 URL。
- 谓词：路径、Host、方法、头、查询、权重、时间窗。不满足就不转发。
- 过滤器：去掉前缀、改写路径、加请求 id、限流、熔断。全局过滤器和路由过滤器按 `Ordered` 排。

令牌校验放在全局过滤器里。通过之后把用户 id 写进头，转发给下游。这个头在进入网关时先清掉，调用方自己带的同名头进不了内网。下游信任的是网关写的头，并且只接受来自网关网段的连接。网关做所有服务都要的事：TLS、限流、鉴权、请求 id、访问日志、转发。进程里留和这个资源有关的事：参数校验、这笔订单是不是这个用户的、幂等。网关不知道订单属主。

登录态有两种放法。

JWT 由登录服务签发，载荷里是用户 id、过期时间和一个 `jti`。RS256 用私钥签名，网关只放公钥，本地验签，不查库。验签通过就把用户 id 写进头。令牌在过期之前一直有效，改密码和踢下线都不会让已经发出的令牌失效。要立刻作废，把还没过期的 `jti` 放进 Redis 黑名单，TTL 设成令牌剩余寿命；或者 access token 只有几分钟，刷新时再查服务端的会话状态。黑名单让每个请求多一次 Redis 读。验签失败是 401。过期和在黑名单里也是 401，不把这两种和「没有权限」混成 403。

服务端会话是登录时生成随机 id，Redis 里存用户和权限，Cookie 或头里只放这个 id。网关每次用 id 取出用户。作废就是删掉这个 key，下一次请求立即失效。代价是每个请求都读 Redis，Redis 不可用时登录态一起不可用。网关对 Redis 超时要有明确失败，不能在查不到 key 时放行。

接入方多、网关要水平扩展、退出登录可以等到令牌过期，用 JWT。权限变更和踢人要马上生效，用服务端会话，或 JWT 加黑名单。下游不再验一遍令牌。能绕过网关直接打到 Pod 的调用，要在网络策略上关掉，而不是让每个服务再实现一套验签。

过滤器里不要调用阻塞客户端。Redis 限流用 Reactive 的 Lettuce。`RequestRateLimiter` 配 Redis 令牌桶时，按用户 id 做键，没有登录态时用 IP。只按 IP 会误伤出口地址相同的一群人。

限流算法按窗口的毛病来选。固定窗口在边界两侧各放进一整窗的量。滑动窗口把当前时间附近的计数加起来。令牌桶按速率补充令牌，允许短突发。漏桶按固定速率放行。分布式计数用 Redis Lua。单机内存里的计数每个副本各算各的。

Nacos 同时做注册和配置。纯 Kubernetes 用 Service 和 CoreDNS 发现，配置用 ConfigMap。虚拟机或混部再用 Nacos。两套发现会给出不同的地址，选一套。在 Kubernetes 里可以只把 Nacos 当配置中心，实例列表交给 Service。

Nacos 2 的客户端走 gRPC。HTTP 端口是 8848 时，客户端 gRPC 是 9848，服务端互相同步还要 9849。临时实例靠心跳，默认是临时的，走 Distro，偏 AP：少数节点不可达时注册仍可用，实例列表可能短暂不一致。持久实例走 Raft，偏 CP。配置数据走 Raft。注册用临时实例：心跳没了，服务器超时后摘掉。进程退出要反注册，杀进程时靠心跳超时兜底，这段时间调用方还会打到已经死的 IP，所以调用方自己的连接超时不能省。

配置的 Data Id 用「应用名-环境.后缀」。加载顺序是共享配置、扩展配置，最后是应用自己的 Data Id，后加载的覆盖先加载的。应用自己的配置优先级最高。命名空间或分组写错时读到空，进程带着默认值启动，故障会表现为连到了错误的库。`@RefreshScope` 在变更时重建 Bean。`@ConfigurationProperties` 可以被重新绑定。已经建立的 Hikari 池不会因为配置文本变了就自动换主机，换地址要重建数据源或滚动。回调里解析失败就保持旧配置。

本地快照在连不上服务器时用来启动。容器根文件系统只读时，把快照目录指到可写的 emptyDir。

---

## 9. 分布式理论

一个进程里用锁和事务就能排好的事，拆到多台机器上之后要面对三件事：消息会丢、会重复、会延迟；机器会停；网络会把集群切成几块。下面这些名词都是在给这三件事选规则。

### CAP、PACELC、BASE

CAP 说的是发生网络分区的时候：分区的两侧不能同时做到「每次读都看到最新写入」和「两侧都继续对外服务」。选一致性，少数派就停止写入，甚至停止服务，这是 CP。选可用性，两侧都继续读写，等分区恢复再合并，读到的可能是旧的，这是 AP。没有分区的时候，延迟和一致性还可以同时往前做。把 CAP 理解成平时也只能三选二，会漏掉「分区才迫使你选」这个条件。

etcd、Nacos 里的持久实例和配置是 CP：没有多数派就不提交。Nacos 的临时实例和 Redis 集群在部分槽位上的继续服务更靠近 AP。分区恢复之后要有合并规则。

PACELC 把没有分区的情况补上：有分区时在可用性和一致性之间选；没有分区时在延迟和一致性之间选。同步复制更一致，提交要等另一台确认，延迟更高。异步复制返回快，另一台还没收到。MySQL 半同步和 Redis 异步复制就是这个选择的两个点。

BASE 是另一端的工程说法：基本可用、软状态、最终一致。软状态是允许中间有一段时间读到旧值或中间步骤。最终一致是在没有新的写入、消息也都送达之后，副本会相同。要有一个时刻用对账把两边对齐，并给这个延迟一个上限。

### 一致性怎么分级

强一致是读到的是上一次成功写入。主从都确认之后再返回，读也打到主，接近这个效果。线性一致还要求所有客户端看到的顺序和真实时间一致，通常要共识算法在多数派上提交。

顺序一致是所有客户端看到同一个操作顺序，这个顺序可以和真实时间略有偏差。因果一致保证有因果关系的操作不被看反。读己之写是一个客户端至少能读到自己刚写的。会话一致把这个保证放在一次会话里。最终一致只保证收敛。

业务上按数据来选。余额和库存的扣减要强一致，用数据库条件更新或单主上的原子操作。时间线、搜索索引、统计可以最终一致，用消息把变更送过去。一个接口里混着两种数据时，把强一致的那部分单独读主库，其余走缓存。

### 多数派和副本

N 个副本，写入要 W 个确认，读取要 R 个确认。W + R > N 时，读集合和写集合一定有交集，能读到最近一次满足 W 的写入。常见是 N = 3，W = 2，R = 2，可以坏一台。W = N 是每写都要全部确认，一台慢就写不进去。W = 1 是最快的写，读必须接触全部副本才能确认自己看到的是最新的。

这是无主副本的算法。MySQL 和 Redis 主从是有主的：写进主，读主就看到这次写入；读从就要接受复制延迟。Kafka 的 `acks=all` 加上 `min.insync.replicas` 是有主的多数派。

法定人数也用来选主。三个节点可以坏一个，五个可以坏两个。两个节点没有多数派，网络一切开就无法决定谁是主，所以哨兵、etcd、Nacos 的 Raft 组都用奇数。

### Paxos

Paxos 要一组进程对一个值做出唯一的决定。进程会停，消息会丢、会重复、会延迟，发送方不会伪造消息。决定做出来之后，后面学到结果的进程都学到这同一个值。

三个角色可以叠在同一台机器上。提议者提出值，接受者投票，学习者得知结果。每个提议有一个编号，所有编号能比大小，并且不重复。轮次放在高位、机器号放在低位。

第一阶段是准备。提议者选一个比自己用过的都大的编号 n，向一个多数派发送准备请求。接受者如果还没有答应过更大的编号，就承诺不再接受编号比 n 小的提议，并把自己已经接受过的、编号最大的那条提议回给提议者。

第二阶段是接受。提议者集齐一个多数派的承诺之后才能发接受请求。值按回包决定：有人已经接受过提议，就用这些回包里编号最大的那个值；谁都没接受过，才用提议者自己原来想提的值。请求是 (n, v)。接受者只要还没有答应过比 n 更大的编号，就接受它。

一个值被选定，是指一个多数派接受了同一个 (n, v)。Basic Paxos 只决定一个值。一条日志是一串这样的决定，每个槽位跑一轮。

安全性来自多数派一定相交，再加上「有旧值就必须沿用」。假设编号 n 的值 v 已经被一个多数派接受。任何编号更大的提议者要进入第二阶段，都得再拿到一个多数派的承诺。这两个多数派至少有一个接受者是同一个人。这个人已经接受过 (n, v)，回包里会带上它；如果他后来又接受了更新的提议，那条更新的提议也是按同一规则从 v 沿用下来的。新提议者必须采用回包里编号最大的值，所以发出去的仍然是 v。对编号做归纳：凡是编号大于 n、并且真能完成的提议，值都是 v。

这套规则不保证某一次提议一定完成。两个提议者可以轮流用更大的编号打断对方，两边都过不了第二阶段。已经选定的值不会因此变成别的值。

工程上先固定一个稳定的提议者。它做一次准备，承诺覆盖后面的一段槽位；每条新命令只跑第二阶段。这是 Multi-Paxos。Raft 把这套做法收成固定角色：强领导者、任期、日志必须连续。Paxos 允许槽位上暂时有空洞，实现者自己补。ZooKeeper 的 Zab、etcd 和 Nacos 持久数据用的 Raft，都是「稳定领导者加上多数派复制」。Chubby 用的是 Multi-Paxos。

### Raft

Raft 是一种共识算法。etcd、Nacos 的持久数据、Kafka 的 KRaft、RabbitMQ 仲裁队列都用它或它的变体。一个任期里最多一个领导者。领导者把日志复制到跟随者，多数派写下之后，这条日志才算提交，然后应用到状态机。

选举：跟随者在选举超时内没收到心跳，就变成候选人，任期加一，向其他节点要票。每个节点在一个任期里只投一票，并且只投给日志至少和自己一样新的候选人。超时时间加随机抖动，避免所有人同时发起选举。少数派凑不齐多数票，选不出领导者。已经提交的日志只会由拿到多数派的那一个领导者继续复制。

日志匹配：同一索引、同一任期的日志在所有节点上是同一条。领导者只追加，冲突时用自己的日志覆盖跟随者。已经提交的日志在以后的领导者里仍然存在。

客户端只向领导者写。领导者还没复制到多数派就宕机时，客户端超时重试。操作本身要幂等，或者用一个客户端 id 和序号，让新领导者认出这是同一笔。Raft 保证的是日志的一致，不代替业务幂等。

### 时钟

物理时钟会漂移，NTP 校正时还可能往回跳。用本机时间做超时和证书判断可以，用两台机器的时间戳决定事件顺序不可以。Redis 锁的 TTL 用的是 Redis 服务器的时间。主从切换仍会把还没复制的锁丢掉，那是复制的问题。

雪花 id 把时间、机器号和序列编进一个 64 位整数：1 位符号，41 位毫秒，10 位机器，12 位序列。单毫秒单机 4096 个，整体趋势递增，适合做主键。机器号从数据库或 Redis 租一块，租约到期才能给别人。时钟回拨时拒绝发号或等到时间追上。UUID v7 也按时间排序，16 字节。UUID v4 是随机的，会把 B+ 树写散。

Lamport 时钟是一个整数，事件发生或收到消息时取最大值再加一。它可以给出一个全序，但看不出两件事是不是因果相关。向量时钟每个进程一维，能判断因果。订单号和日志的时间用单点生成的雪花 id，或者数据库的自增，不拿各台应用机器的 `System.currentTimeMillis()` 拼顺序。

租约是带期限的所有权。持有者在期限前续约，到期没续，别人就可以拿走。Kubernetes 的选主、Kafka 的组成员、Redisson 的看门狗都是租约。期限必须大于时钟误差和处理时间，续约失败要当成已经失去所有权，停下手上的独占操作。

### 失败的样子

宕机是进程停了，内存里的状态没了。恢复靠磁盘上的日志和启动时的重放。网络分区是两边都活着但不通。这时候最容易出现两个写入点。防护是少数派没有法定人数就不写。

脑裂是分区两侧各有一个主。Redis 哨兵要求多数派才故障转移。旧主如果还在对外服务，客户端可能继续往旧主写。哨兵把旧主改成从、并要求从只读，是为了让它重新连上之后丢掉分叉的写入、向新主对齐。应用在切换窗口会写失败，按幂等重试到新主。Hikari 的 `keepalive-time` 把池里指向旧主的连接清掉。

重复和乱序一起出现。消息至少一次，所以会重复。不同分区之间没有顺序。消费者按业务键去重，用状态机接受「取消先到、支付后到」这种乱序：状态只能沿允许的边走，走不通的消息先放着或进死信，不能把状态从已支付改回未创建。

幂等是这些失败模型的落点。同一笔请求执行多次，系统状态和执行一次相同。数据库唯一键、条件更新、幂等表、带 token 的锁释放，都是在实现这句话。

---

## 10. 系统设计

设计题先把约束问清楚，再估容量，再画主路径，最后把失败和一致性补上。组件从前面几节里选。

### 怎么拆一道题

先问清楚这几件事：读多还是写多，峰值 QPS 和平均差多少，数据要留多久，能不能接受旧数据，有没有热点，用户在一个地域还是多个。问完才能决定要不要缓存、要不要分片、要不要异步。

然后做数量级估算。每秒请求数乘以单次读写的大小得到带宽。每天的新增行数乘以保留天数得到存储。峰值按平均的几倍到几十倍留余量，秒杀这种脉冲要单独算。连接数按副本数乘以每进程的 Hikari 上限，对到数据库的 `max_connections`。估算是为了选出瓶颈在 CPU、网络、磁盘还是锁。

接口和数据模型接着写。谁是聚合根，哪些查询要同时返回，哪些可以延迟。能用一张表和一个事务完成的，不拆成三个服务。

主路径画一条写、一条读。写路径标出事务边界和发件箱。读路径标出缓存层级和回源。然后单独写失败：下游超时怎么办，消息重复怎么办，缓存和库不一致怎么收口，扩容时数据怎么搬。观测放在最后：这条路径的延迟、错误率和积压看哪几个数。

延迟的预算按层扣。入口 3 秒的时候，里面每一跳都是几十到几百毫秒，数据库一次查询控制在几毫秒到几十毫秒。某一跳占掉大部分预算，就把它变成异步，或者加缓存。

### 几个反复出现的构件

id 在单库里用自增主键。要在应用里生成时用雪花 id，机器号用租约。时钟回拨时拒绝发号。

限流算法按窗口的毛病来选。固定窗口在边界两侧各放进一整窗的量。滑动窗口把当前时间附近的计数加起来。令牌桶按速率补充令牌，允许短突发。漏桶按固定速率放行。分布式计数用 Redis Lua，本地突发用进程内令牌桶或 Sentinel 先挡一层。

一致性哈希把键分配到一批节点上，节点增减时只搬一小段数据。哈希空间收成一个环。每台物理节点用多个种子算出很多位置，这些是虚拟节点。一个键哈希之后，沿环顺时针遇到的第一个虚拟节点就是它的归属。

三台机器 A、B、C，每台在环上有很多点。键 k 先碰到 A 的某个虚拟节点，就由 A 负责。A 下线时，原先落在 A 那些点上的键继续顺时针走到 B 或 C，其他键不动。没有虚拟节点时，A 离开会把一整段弧交给下一家。虚拟节点把这段弧切碎，每台一两百个就把倾斜压下去。权重大的机器放更多虚拟节点。

缓存节点用这套环，键是缓存 key。分库也可以用它：键是用户 id，节点是分片。取模在分片从 8 扩到 9 时几乎每行都要搬；扩到 16 这种两倍才只搬一半。一致性哈希在任意增减一台时只搬这一台名下的键。节点抖动时键会来回搬，打穿数据库。离开前先把流量摘干净，或者每个键沿环再找下一台做备份。没有本地状态的请求用轮询，Spring Cloud LoadBalancer 默认就是轮询。

对账是最终一致的闭环。每隔一段时间用源表的聚合和目标对一下：订单数和库存扣减数、发件箱已发送和消费者处理数、缓存里的库存和数据库的库存。偏差超过阈值再告警。

### 秒杀

普通购买是查商品、建单、扣库存、付款、发货。秒杀是活动页、详情、立即抢、去付款。特点是价格低、瞬时并发高、限时限量、要预热。

读和写分开。详情走缓存或静态页。写的本质是并发扣减，要保证不超卖。秒杀接口单独路由、单独副本，限流放在网关上，超过的返回排队页。验证码和答题放在真正扣库存之前，把脚本流量挡在外面。

活动开始前把库存写进 Redis。扣减是一段 Lua。售罄标记如果只放在某个 JVM 的内存里，别的进程看不到。标记的来源是 Redis：Lua 返回售罄，或者订阅一个售罄频道。Pub/Sub 不留存，进程刚启动时本地标记是空的，第一批请求打到 Redis。

限购和扣减放进同一段 Lua，避免扣完又发现这个用户已经买过，还得把库存加回去：

1. 请求 id 已有结果，直接返回上次的结果。
2. `SET 用户限购键 1 NX EX 活动时长`。设不上就是已经买过。
3. 库存大于 0 才 `DECR`，否则删掉限购键并返回售罄。
4. 把用户、SKU、请求 id 放进队列。队列满了就 `INCR` 还库存、删限购键，接口返回稍后再试。

```java
DefaultRedisScript<Long> script = new DefaultRedisScript<>();
script.setScriptText(DECR_IF_POSITIVE);
script.setResultType(Long.class);
Long left = stringRedisTemplate.execute(script, List.of("stock:" + skuId));
```

消费者按数据库能承受的速度建单。订单表的唯一键是用户 id + 活动 id + SKU，或请求 id。插入成功再写扣减流水。碰到唯一键就是重复投递，确认消息即可。数据库故障则不确认，稍后再消费。多次仍失败就把库存还回 Redis，并记入失败表。Spring 事务包住「插订单 + 写幂等键」，消息的 ack 放在事务提交之后。提交成功但 ack 之前进程挂了，消息会再来，唯一键把第二次变成空操作。

定时任务用「预热库存 − 成功订单数」和 Redis 当前值比较。活动结束后以订单表为准做一次对账。

热点 SKU 的一个 key 会打在集群的一个槽上。库存拆成 `stock:{sku}:0` 到 `stock:{sku}:9`，请求随机选一段，这一段空了再试别的段。哈希标签 `{sku}` 让这些键落在同一个槽，一段 Lua 里可以连续尝试。拆段之后总库存是各段之和，展示用短缓存。

有人绕过接口直接改库时，缓存不会跟着变。Canal 伪装成 MySQL 的副本去拉 binlog。MySQL 要 `binlog_format=ROW`、`binlog_row_image=FULL`，账号有复制权限，`server_id` 不和现有副本冲突。位点要自己存，断线从位点续上。也可以让 Canal 把变更推进 Kafka，应用只做消费者。解析到库存或价格变更后删缓存。这件事放在订阅进程里，不放在下单请求里。

### 搜索

搜索走 Elasticsearch，下单和库存走 MySQL。倒排索引按词指向文档 id。中文用 IK：`ik_max_word` 切得细，`ik_smart` 切得粗。7.x 起类型固定为 `_doc`。Java 客户端用 `elasticsearch-java` 这一支。`RestHighLevelClient` 在 7.15 弃用，8 里移除。

商品索引：标题、关键词、副标题用 `text` 加 IK；品牌名、类目名、图片 URL 用 `keyword`；价格、销量、上架时间、是否有货用数值、日期、布尔。属性是一对多，映射成 `nested`。普通 object 数组会被拍扁，`颜色=黑` 和 `尺寸=L` 会错配成交叉组合。

筛选放进 `bool.filter`：品牌、价格区间、有货、类目。`filter` 不参与打分，可以被缓存。关键词放进 `bool.must` 的 `multi_match`。用户选了价格或销量，就按那个字段排。分页用 `search_after`：排序字段里带一个唯一值，下一页把上一页最后一条的 sort 传回去。`from + size` 超过 `index.max_result_window`（默认 10000）会被拒绝。

品牌和属性筛选项是聚合。bucket 是分组，metric 是组上的统计。nested 属性要先进入 nested 文档再 terms。高亮用 `highlight`，`fragment_size` 控制片段长度。

商品保存成功后发消息，消费者写 ES，失败可重试。搜索允许秒级延迟。429 和 503 退避重试，映射错误进失败队列。更新用 `if_seq_no` 和 `if_primary_term`：序号不一致就重新读再写。

默认 `refresh_interval` 是 1 秒，写完立刻搜可能还看不到。主分片数在索引建好后要改，就建新索引再切别名。查询可以打在副本上，写入打在主分片再同步。别名指到当前索引，对完数再原子拨过去。改分词器或已有字段的类型都要重建索引。

Master 管元数据和分片分配，Data 节点存分片并承担读写。应用连接超时设短，搜索读超时按慢查询上限设。聚合限制 `size` 和 `timeout`。客户端超时取消不了已经在节点上跑的查询。

### 容量不够时的顺序

先做单机能做的：索引、SQL、Hikari、慢查询、缓存、无用的同步调用。然后加副本，应用无状态，数据库加读副本。写仍打在主库上，写成为瓶颈时再分库分表。异步把不是主路径的写移出请求。每一步都要能说出现在的瓶颈数字，再选下一步。

---

## 11. 可观测性

可观测性是从系统外面发出的信号还原里面发生了什么。日志、指标、追踪是三种信号。只有日志时，要靠人去翻一次请求。只有平均值时，看不到慢的那百分之一。三者用同一个请求 id 串起来。

服务看四个信号：流量、延迟、错误、饱和度。延迟用直方图的分位。错误看 5xx 和业务错误码。饱和度看 Hikari 活跃连接和等待线程、Tomcat 忙碌线程、队列 lag、CPU 节流。资源看使用率、饱和度和错误：磁盘 `%util` 和 await，内存 available，文件描述符。

RED 是给服务的：Rate、Errors、Duration。USE 是给资源的：Utilization、Saturation、Errors。两边一起看，才能分清是自己慢还是依赖慢。

### 日志

日志打到 stdout，一行一个 JSON。字段固定：时间、级别、服务名、请求 id、trace_id、span_id、路径、状态码、耗时、错误码。正文是消息，不把整份请求体打进去。手机号、令牌、密码不进日志。SLF4J 是门面，Logback 是实现。格式用 logstash 编码器或自己写的 JSON layout。

请求 id 放在 MDC 里。Micrometer Tracing 会把当前 span 的 trace_id 放进 MDC，日志平台才能从一条错误跳到整条追踪。异步线程和线程池要靠 `TaskDecorator` 把 MDC 拷过去。异常用带异常参数的 `error` 留下栈，栈在日志系统里，不在 HTTP 响应里。

访问日志每个请求一条，在过滤器里打，包含状态码和耗时。业务日志只在状态变化和外部调用失败时打。循环里逐条打，流量一上来就会把采集管道打满。级别在生产用 INFO，排查时对单个服务临时开 DEBUG，用完收回。

异步 Appender 的队列满了可以阻塞或丢弃。`neverBlock=true` 会丢日志。审计类日志用同步写出，或者队列满了就阻塞调用方。进程被 `SIGKILL` 时，队列里还没刷出的行会丢。

### 指标

指标是可聚合的数字。Micrometer 是门面，Prometheus 注册表把它们暴露在 `/actuator/prometheus`。三种类型就够用：

- Counter 只增。请求数、错误数。看速率用 `rate()`，看增长用 `increase()`。
- Gauge 可升可降。正在处理的请求数、Hikari 活跃连接、队列长度。
- Timer 观察耗时。分位用 Prometheus 的 `histogram_quantile`。要跨副本聚合，就打开 `publishPercentileHistogram`。客户端自己算的分位（`publishPercentiles`）不能在服务端相加，副本一多就失真。

桶的边界按 SLO 设，例如 50 毫秒、100 毫秒、300 毫秒、1 秒、3 秒。标签是有限集合：服务、路由模板、状态码、下游名字。用户 id、订单 id 放进标签，基数会把 Prometheus 打满。路由用模板 `/orders/{id}`。

Boot 的 HTTP 计时器只知道路由慢，不知道慢在下游还是慢在池。补上 Hikari 等待时间、Redis 命令超时次数、发件箱堆积行数、Lua 返回的售罄次数。JVM 的堆、GC 停顿、线程数用 Micrometer 的 JVM 绑定，和 cgroup 的内存、CPU 节流放在同一张图上。堆很满而 cgroup 还空，是 `MaxRAMPercentage` 设小了。cgroup 先 OOM 而堆还空，是直接内存或线程栈。

采集路径不要和业务抢同一个端口上的重活。Prometheus 拉取 `/actuator/prometheus` 的间隔按 15 秒到 1 分钟。管理端点和业务端点的认证分开，指标端口不对公网开放。

### 追踪

一条追踪是一次请求穿过所有服务的树。span 是树上的一个节点：一次 HTTP 调用、一次 SQL、一次 Redis。span 上有开始时间、结束时间、状态和几个属性。父 span 的 id 放在 `traceparent` 头里往下游传。

Spring Boot 3 用 Micrometer Tracing，不再使用 Spring Cloud Sleuth。桥可以是 OpenTelemetry，也可以是 Brave。采样概率是 `management.tracing.sampling.probability`，默认 0.1。上游已经采样时，下游跟着采样。导出到进程外的 OpenTelemetry Collector，应用不直接对后端的地址负责。

另一种接法是挂 OpenTelemetry 或 SkyWalking 的 Java agent，`-javaagent` 在启动时织入 JDBC、HTTP 和 Redis。agent 和 Micrometer Tracing 一起开，同一条调用会出现两份 span。选一个。国内存量系统里 SkyWalking 的 agent 仍然常见，新系统更常见的是 OTel agent 或 Micrometer Tracing。两个 agent 不要同时挂。

采样在流量大时按比例，错误和慢请求要能留下。头部采样在请求进来时就决定，可能把一次慢请求丢了。尾部采样等整条追踪结束再决定，能保住错误，代价是收集器要先把追踪攒住。入口先用按比例加「错误一定采样」。

span 上只放定位需要的属性：订单 id、下游地址、重试次数。SQL 可以记语句模板，不记绑定进去的隐私参数。

### SLO 和告警

SLI 是测量出来的比例，例如 30 天里耗时小于 300 毫秒且不是 5xx 的请求占比。SLO 是这个比例的目标，例如 99.9%。错误预算是 1 减去 SLO，预算烧完就停止普通发布，先处理可靠性。

告警打在症状上：错误率、延迟分位、预算燃烧速度、队列 lag、探针失败。CPU 高本身不叫醒人，它引起的延迟才叫醒人。燃烧速度用两个窗口一起看，短窗口抓突发，长窗口抓慢性消耗。

每个告警带一个下一步：看哪张仪表盘，先查哪个下游。就绪失败和存活重启分开告，前者是依赖，后者是进程自己。`OOMKilled` 和 Java 堆 OOM 分开告，对应的动作一个是加内存或查泄漏，一个是看堆 dump 和 `MaxRAMPercentage`。

### 接口变慢怎么查

先分清范围。看入口的流量、延迟分位和错误率：是全站一起变慢，还是一条路由、一个用户。时间和最近一次发布对齐。全站的 5xx 先看网关到 Pod 的连接失败和就绪探针，再看应用。

然后看饱和。Hikari 活跃连接和等待线程、Tomcat 或虚拟线程的堆积、正在处理的请求数。池等待升高，请求堆在应用里，原因在池子后面的数据库、Redis 或下游。

用这一条慢请求的 trace 看时间花在哪个 span。SQL 长，就去数据库上看锁和扫描行数：`sys.innodb_lock_waits`、慢查询、`EXPLAIN` 里的 `rows`。行锁等待和死锁对应 1205 和 1213。Redis 长，看 `slowlog`、是不是在 `bgsave`、是不是一个大 key。下游 HTTP 长，看对方的错误率和超时，Sentinel 或 Resilience4j 的熔断有没有打开。

线程堆在哪，用 `jstack` 或 `jcmd Thread.dump_to_file`。大量 `BLOCKED` 在等同一把监视器，是锁。大量 `WAITING` 在 `parking`，多半是池或 RPC。GC 停顿和堆占用放在同一张图上：停顿长而 cgroup 内存还空，是堆里的 GC；退出码 137 且原因是 OOMKilled，是 cgroup。`container_cpu_cfs_throttled_seconds_total` 在涨，同时延迟升高，是 CPU limit 在节流。

依赖都正常、只有新版本变慢，就回滚镜像，再对这条路由做剖析。回滚解决不了数据库里已经变差的执行计划。

---

## 12. 云平台

云把机器、网络、负载均衡和托管数据服务按 API 提供出来。区域是地理位置上的一组数据中心，可用区是区域内电力和网络独立的一块。同可用区延迟低，一台可用区的故障不会直接拆掉另一台。跨区域延迟高一个数量级，用来做容灾，不用来做同步调用。

应用无状态，可以放在任一可用区。状态放在托管的数据库、缓存和对象存储里。JVM 里不保存要跨请求保留的数据，上传的文件进对象存储，会话进 Redis 或做成签名令牌。

### 网络和入口

VPC 是一块隔离的私有网络。子网按可用区放，应用子网和数据子网分开。安全组是有状态的防火墙，最小权限打开：入口只接受负载均衡的流量，数据库只接受应用安全组，管理端口不对公网开放。出网走 NAT，实例不必带公网地址。

负载均衡放在至少两个可用区。健康检查打就绪接口，失败阈值和 Kubernetes 的探针错开一点，避免同一瞬间两边一起摘除。TLS 在负载均衡上终止。静态资源和上传文件走对象存储加 CDN，应用只签发上传地址。

域名解析到负载均衡。内部服务用云的内网 DNS 或 Kubernetes 的 Service。IP 在扩缩容和故障转移时会变。

### 托管数据服务

关系库用多可用区的主备。应用写主、读可以走只读实例，读己之写的查询留在主上。备份自动做，恢复时间要试过。参数和前面 MySQL 那一节一致：`utf8mb4`、`ROW`、提交时刷 redo 和 binlog。连接数按所有应用副本的池子加起来设，托管实例的最大连接数是选规格时的一个上限。故障转移时所有 Pod 会同时重连，Hikari 的 `connection-timeout` 和抖动避免把新主瞬间打满。`keepalive-time` 清掉指向旧主的半开连接。

缓存用托管 Redis，持久化和淘汰策略按用途设。主从切换会有短暂停写。Lettuce 打开拓扑刷新，地址不写死。内存打满会触发淘汰或拒绝写，监控 `maxmemory` 的使用率。

对象存储按桶划分权限。应用用角色拿到临时凭证，不把长期密钥放进镜像或环境变量文件。私有桶通过短时签名 URL 给浏览器上传或下载。桶可以跨区域复制，用来做备份，复制有延迟。

消息队列如果用云上的托管 Kafka 或 RocketMQ，投递语义和自建相同。换的是磁盘、副本和扩分区由平台做。积压、幂等和死信仍是应用的责任。

### 计算和发布

容器服务跑在 Kubernetes 上。节点组分布在多个可用区，`topologySpreadConstraints` 按可用区打散。一个可用区不可用时，另一个区的副本还在，配合 PDB 和至少两个副本。

发布用不可变镜像。构建在流水线里完成，集群只拉取。回滚是把 Deployment 的镜像标签指回上一个提交。数据库迁移作为发布前的 Job，并且向后兼容：先加列和双写，再切读，再停旧列。先做不兼容的 DDL 再滚动应用，滚动的中途旧代码会写失败。

配置和密钥放在平台的密钥管理或 Kubernetes Secret 里，由卷挂进容器。环境变量适合非机密的开关。密钥进了环境变量就会出现在崩溃转储和 `/proc/<pid>/environ` 里。挂成文件、只给进程读，范围更小。JVM 参数放在环境变量 `JDK_JAVA_OPTIONS` 或入口命令里，和业务配置分开，避免一次改配置把堆参数冲掉。

自动扩缩看饱和度。定时扩容用在知道脉冲何时到来的场景，例如活动开始前把秒杀的副本拉起来，HPA 在脉冲已经打进来之后才反应。缩容设稳定窗口。Java 进程的 RSS 下降很慢，按内存缩容会在 RSS 还没下来时来回扩。

成本按三个数看：副本是不是远高于实际 QPS，日志和追踪是不是全量采集，跨可用区流量是不是因为把数据库和所有应用放在了不同的区。同可用区优先放同一组会互相调用的服务。规格从能撑住连接数和堆外内存的最小档开始。

### 权限和故障

人和服务都用角色，不用共享的长期密钥。流水线发布用的角色只能推指定仓库和滚动指定命名空间。应用的角色只能访问自己的桶和自己的库账号。人的排障角色默认可读，写生产要单独提升，并留下审计。

可用区故障时，负载均衡把流量送到剩下的区，数据库切到备用区。应用要能在写失败时重试幂等请求，并在连接被重置后重建池里的连接。

同城两个可用区之间可以做同步复制：MySQL 半同步、托管库的多可用区主备，延迟还在毫秒级。两地三中心的第三个副本放在另一座城市，只做异步复制，RPO 是秒到分钟。跨地域的确认不放进下单事务。切到异地是运行手册里的决定，事先演练过；应用靠幂等重试接住切换窗口，靠读主库接住自己的写入。区域级故障期间，异地入口可以只读或降级，不在故障发生时现想切换步骤。

多租户如果跑在同一个集群里，用命名空间、NetworkPolicy 和资源配额分开。一个租户的请求打满节点，会让同节点的其他 Pod 被节流或驱逐。重要服务用独立的节点组。Java 的 CPU 节流会先表现为 GC 停顿变长，再表现为接口超时。

---

## 13. 安全底线

鉴权放在网关，授权放在资源上。网关证明「你是谁」。订单服务证明「这行是你的」。两件事都做，只做前面那件，换一个订单 id 就能读到别人的数据。

### 查询和越权

MyBatis 的 `#{}` 是预编译参数，驱动负责绑定。`${}` 是字符串拼接，只用于已经白名单校验过的列名、表名，不能接用户输入。`IN` 列表用 `<foreach>` 配 `#{}`，不把一串 id 连成一段 SQL。

Mapper 挡不住越权。按主键 `selectById` 只要行在就返回。查询条件里带上当前用户，用户 id 来自网关写入的头，或者来自方法参数里已经验过的主体，不来自请求体里的用户字段：

```java
Order order = orderMapper.selectByIdAndUser(orderId, userId);
if (order == null) {
    throw new ResponseStatusException(HttpStatus.NOT_FOUND);
}
```

不存在和不属于当前用户都返回 404，避免用 403 告诉对方这个 id 是有主的。管理接口走单独的角色和路由。列表同样按用户过滤。

### 密码和令牌

密码只存单向哈希。`PasswordEncoder` 用 bcrypt 或 argon2，把一次校验调到几十毫秒。SHA-256、MD5 可以穷举，不能当密码哈希。校验用编码器的 `matches`，它按固定时间比较。

登录失败和账号不存在返回同一句话。日志里不写密码、验证码和完整令牌。令牌的签发和注销按网关那一节。内部服务之间用单独的身份，不把用户的访问令牌转发到和这次请求无关的下游。

### 上传和向外的请求

上传限制大小，先看 `Content-Length`，再限制实际读到的字节数。文件名由服务端生成，用户提供的文件名只做下载时的展示，并且去掉路径和换行。类型用文件内容判断。对象放在私有桶，下载用短时签名 URL。业务 Pod 的磁盘不留用户文件。

服务如果要按用户给的地址去拉取，就有 SSRF。地址先解析成 IP，再判断是不是回环、链路本地、私网和云元数据地址 `169.254.169.254`。不在允许的域名里就拒绝。重定向要逐跳重新检查。`RestClient` 的超时和重定向次数都设上。能用固定的上游就不要接收任意 URL。

Actuator、交互式文档和默认管理端口在生产只对内网开放，和业务端口的认证分开。错误响应不带栈和 SQL。密钥用角色的临时凭证，不进镜像。

---

## 依据

- [InnoDB 锁](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking.html)，[MVCC](https://dev.mysql.com/doc/refman/8.4/en/innodb-multi-versioning.html)，[半同步](https://dev.mysql.com/doc/refman/8.4/en/replication-semisync-interface.html)
- [HikariCP](https://github.com/brettwooldridge/HikariCP)，[MyBatis](https://mybatis.org/mybatis-3/zh_CN/index.html)
- [Spring 事务](https://docs.spring.io/spring-framework/reference/data-access/transaction.html)，[Spring MVC](https://docs.spring.io/spring-framework/reference/web/webmvc.html)
- [Spring Boot 3.4 发布说明](https://github.com/spring-projects/spring-boot/wiki/Spring-Boot-3.4-Release-Notes)（优雅停机改为默认），[优雅停机](https://docs.spring.io/spring-boot/reference/web/graceful-shutdown.html)，[虚拟线程](https://github.com/spring-projects/spring-boot/blob/v3.5.13/spring-boot-project/spring-boot-docs/src/docs/antora/modules/reference/pages/features/spring-application.adoc)，[Kubernetes 探针](https://docs.spring.io/spring-boot/reference/actuator/endpoints.html)
- [Redis 持久化](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)，[集群规格](https://redis.io/docs/latest/operate/oss_and_stack/reference/cluster-spec/)，[分布式锁](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/)
- [Lettuce 拓扑刷新](https://github.com/redis/lettuce/blob/master/src/main/java/io/lettuce/core/cluster/ClusterTopologyRefreshOptions.java)，Spring Boot 的 `spring.data.redis.lettuce.cluster.refresh` 默认不打开周期刷新
- [JEP 444 虚拟线程](https://openjdk.org/jeps/444)，[JEP 491](https://openjdk.org/jeps/491)（JDK 24，`synchronized` 不再钉住载体），[JEP 506 Scoped Values](https://openjdk.org/jeps/506)（JDK 25）
- [分层镜像与 JarLauncher](https://docs.spring.io/spring-boot/docs/3.2.x/reference/html/container-images.html)（3.2 起为 `org.springframework.boot.loader.launch.JarLauncher`）
- [RabbitMQ 仲裁队列](https://www.rabbitmq.com/docs/quorum-queues)，[Kafka 复制与高水位](https://kafka.apache.org/documentation/#replication)，[Kafka 投递语义](https://kafka.apache.org/documentation/#semantics)
- [ClassLoader](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/ClassLoader.html)
- [Kubernetes Pod 生命周期](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/)，[持久卷](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)
- [Sentinel](https://sentinelguard.io/zh-cn/docs/introduction.html)，[Seata AT](https://seata.apache.org/docs/dev/mode/at-mode/)
- [Nacos 2.x 端口](https://nacos.io/docs/latest/guide/admin/deployment/)
- [Paxos Made Simple](https://lamport.azurewebsites.net/pubs/paxos-simple.pdf)，[Raft](https://raft.github.io/)
- [gRPC 核心概念](https://grpc.io/docs/what-is-grpc/core-concepts/)，[JWT](https://datatracker.ietf.org/doc/html/rfc7519)
- [Micrometer Tracing](https://micrometer.io/docs/tracing)，[OpenTelemetry Java](https://opentelemetry.io/docs/languages/java/)

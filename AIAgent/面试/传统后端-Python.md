# 传统后端学习笔记（Python）

一份 Python 服务从进程到上线的知识体系。业务数据以 MySQL 为准，Redis 承担缓存、计数和原子扣减，Elasticsearch 承担搜索，消息队列承担异步和削峰，网关承担入口的鉴权和限流，容器和 Kubernetes 负责交付。

库以仍在维护的为准：`redis`（redis-py）、`elasticsearch` 8 的 DSL、`nacos-sdk-python` v2、`rocketmq-python-client` 5.x、SQLAlchemy 2.0、`asyncmy`、`mysql-replication`、`aio-pika`、`aiokafka` 或 `confluent-kafka`。

一条写请求的路径：网关校验令牌并限流，FastAPI 生成请求 id，Lua 在 Redis 里做原子判断，发件箱和业务行在同一个 MySQL 事务里提交，后台循环把发件箱推到队列，消费者更新搜索索引或创建下游单据。读请求先看进程内短缓存，再看 Redis，最后查库并回填。

---

## 1. 进程与并发

CPython 默认有 GIL：同一时刻只有一个线程在执行 Python 字节码。线程在等网络、磁盘、Redis 时会放开 GIL，所以 IO 密集用线程或协程都有效。纯计算（压缩、加密、大循环）拿不到第二个核，要开进程。

3.13 起有 free-threaded 构建（`python3.13t`，编译选项 `--disable-gil`）。`sys._is_gil_enabled()` 看当前是否真的关掉了 GIL。没标成支持 free-threading 的 C 扩展一导入，GIL 会被重新打开。默认发布版仍然开着 GIL。上线按「默认有 GIL」设计，free-threaded 只作为可选加速，多进程仍然是 CPU 并行的办法。

三层并发，各管一件事：

- `asyncio` 在一个线程里切换协程。适合大量同时在等的连接：HTTP、Redis、数据库。一个事件循环卡住（同步的 `requests`、`time.sleep`、没释放的锁）会拖住这个循环上的所有请求。阻塞函数用 `asyncio.to_thread` 丢出去。
- `threading` 适合少量阻塞库，或者和协程混用。`threading.Lock` 保护复合操作。`RLock` 允许同一线程重复获取。标准库没有读写锁；free-threaded 下读多写少再用 `readerwriterlock` 这类包才有意义。GIL 还在时，一把普通锁通常够用。
- `multiprocessing` 或 `concurrent.futures.ProcessPoolExecutor` 做 CPU 密集。每个进程一份解释器和内存，传参走 pickle，大对象和连接池不能直接塞进子进程。进程数按核数的 1 到 2 倍，再多只是上下文切换。

可见性的实际规则：GIL 让单个字节码看起来原子，`i += 1` 这种复合操作仍然会丢更新。共享状态放进 `queue.Queue`，或者用锁包住一小段。

进程内同步：

| 要做的事 | 用法 |
| --- | --- |
| 互斥 | `threading.Lock`。协程之间用 `asyncio.Lock`，它只挡住同一个事件循环上的其他协程，线程仍然可以跑别的任务 |
| 限流，同时 n 个 | `threading.Semaphore(n)` 或 `asyncio.Semaphore(n)` |
| 等一组任务都完成 | `asyncio.gather`，或 `asyncio.Event` 加计数。3.11 起优先用 `TaskGroup` |
| 一组任务到齐再一起走，并且能重复用 | `threading.Barrier` |
| 有界队列，满了堵住 | `queue.Queue(maxsize=n)`，`n` 是正整数。协程用 `asyncio.Queue(maxsize=n)` |
| 不限制长度 | `maxsize=0`。这是不设上限，队列会一直堆积 |
| 带返回值的后台任务 | `concurrent.futures.Future`，或 `asyncio.create_task` |
| 停机 | 线程用 `threading.Event`，阻塞点用带超时的 `wait`，或往队列里放哨兵。协程取消 `Task`，在 `finally` 里清理 |

线程没有安全打断点。别的线程只能 `event.set()`，干活的线程在循环和每次阻塞返回后看这个标志，自己走完清理再退出。只设标志不够，因为线程可能正堵在 `queue.get()` 上，所以 get 要带超时，或者往队列里丢一个哨兵值。

请求进程不做大计算。计算丢给 `ProcessPoolExecutor`，或者丢进队列让独立 worker 做。请求进程只负责收下任务、返回「处理中」。

生产上一个容器里跑一个服务进程，水平扩展交给副本数。一个 Pod 里要开多个 worker 时，用 Uvicorn 自带的 `--workers`（子进程是 spawn，Windows 也能用），或者 Gunicorn 预派生加上 `uvicorn-worker` 包里的 worker 类。`uvicorn.workers` 模块已标记为后续会删。每个 worker 是完整的一份应用：导入、连接池、本地缓存各有一份。副本数乘以 worker 数才是总进程数，内存按这个乘。CPU 密集的服务把 worker 数压到接近核数。纯异步 IO 常见做法是一个 Pod 一个 worker，靠副本数扩。

Gunicorn 的 master 是 arbiter，worker 挂了由它拉起。Uvicorn 多进程下，`SIGHUP` 逐个重启 worker 以便加载新代码，`SIGTTIN` / `SIGTTOU` 加减 worker 数量（Windows 上没有这组信号）。收到 `SIGTERM` 要停接收新请求、等在途请求结束再退出。`terminationGracePeriodSeconds` 要大于这段等待。

### 事件循环

协程函数调用后只得到协程对象，要到 `await`、`asyncio.run` 或 `create_task` 才排进循环。`create_task` 把协程包成 `Task`（`Task` 是 `Future` 的一种），循环下一轮才开始执行，执行到第一个 `await` 把控制权交回去。同一时刻这个线程上只有一个协程在往下走，其余都停在某个 `await` 上。

循环背后是操作系统的多路复用（Linux 上是 epoll）。socket 还没可读就登记进去，线程去做别的就绪协程。所以等 Redis、等 MySQL、等 HTTP 能叠很多。`time.sleep`、`requests.get`、同步的 `redis.Redis`、大段纯 Python 计算不会登记，循环就停在那里。

`TaskGroup` 把一组任务收成一个作用域：其中一个抛错，其余被取消，退出 `async with` 时全部结束。`gather` 要自己传 `return_exceptions` 才不会漏掉失败任务。超时用 `asyncio.wait_for` 或 `asyncio.timeout`，超时之后任务会被取消。

`CancelledError` 继承 `BaseException`，`except Exception` 接不住。清理写在 `finally`，或者捕获之后再重新抛出。吞掉取消会让关闭过程停不下来。`asyncio.shield` 只保护内层任务不被外层取消带走，内层自己该停还是要停。

`contextvars` 会拷进新的 `Task`。请求 id、当前用户放这里。线程池里的函数拿不到这份上下文，除非用 `contextvars.copy_context().run`。

`asyncio.to_thread` 使用循环上的默认线程池，给偶尔的阻塞调用。压缩、加密、批量算特征进 `ProcessPoolExecutor`。Linux 上进程启动方式默认仍可能是 `fork`。应用已经开了线程（Uvicorn、gRPC、客户端后台线程）之后再 fork，子进程只剩发起 fork 的那条线程，锁可能永远停在被占住的状态，子进程卡死。池子用 `multiprocessing.get_context("spawn")` 创建。

跨线程碰循环上的对象，用 `loop.call_soon_threadsafe`。`asyncio.Queue` 不是线程安全的。线程和协程之间用 `janus`，或者 `loop.call_soon_threadsafe(queue.put_nowait, item)`。

停机顺序固定为四步：先拒绝新请求，再等待已经进来的请求（带超时），然后关闭 Redis、数据库、搜索客户端，最后取消还挂着的任务。关连接放在取消之前，避免任务在 `finally` 里还去用一个已经关掉的客户端。

---

## 2. MySQL

业务行存在 InnoDB 里。驱动负责连接、事务和 SQL。SQLAlchemy 2.0 是应用侧的入口，异步驱动用 `asyncmy`，连接串形如 `mysql+asyncmy://user:pass@host/db?charset=utf8mb4`。

### 索引

InnoDB 用 B+ 树。主键索引的叶子是整行，二级索引的叶子是索引列加主键，查完整行要回表。主键用自增整数，随机主键会让页从中间裂开。联合索引从左到右用，范围条件右边的列用不上索引做查找，但可以做覆盖索引或索引下推。

`EXPLAIN` 看 `type`（从好到差大致是 `const`、`eq_ref`、`ref`、`range`、`index`、`ALL`）、`key`、`key_len`、`rows`、`Extra`。`Using index` 是覆盖索引，`Using index condition` 是索引下推。`rows` 是估算。`Using filesort`、`Using temporary` 说明排序或去重没走索引。`SELECT *` 加大范围时，优化器经常放弃索引，因为回表次数太多。

分页用上一页的主键：`WHERE id > ? ORDER BY id LIMIT 10`。`LIMIT 100000, 10` 会先扫过前面的行再丢掉。Join 让小表驱动，连接列要有索引。没有索引时走块嵌套循环，驱动表按 join buffer 分块，每块和被驱动表全表比。

`count(*)` 在 InnoDB 上会扫行或扫索引。只要大约数时用 `SHOW TABLE STATUS` 的 `Rows`，它是估算。精确总数如果很贵，就维护在别的存储里，并接受延迟。慢查询打开 `slow_query_log`，阈值从 1 秒收到几百毫秒再看。

`utf8mb4` 才能存完整的 Unicode。MySQL 里名为 `utf8` 的字符集最多三字节，生僻字和 emoji 会在写入时失败。连接串上的字符集和表一致。

### 事务、隔离与锁

ACID：一个事务里的写入同成同败；提交之后重启还在；并发事务按隔离级别决定能看见什么。

四个隔离级别：读未提交、读已提交、可重复读、串行化。InnoDB 默认是可重复读，用 MVCC 让普通读不必互相等锁。行上有隐藏的 `trx_id` 和 `roll_pointer`，旧版本进 undo 链。可重复读在事务第一次一致性读时生成 Read View，之后普通 `SELECT` 都看这一份。读已提交每次一致性读都生成新的 Read View。

普通 `SELECT` 是快照读，不加锁，看到的是视图里的版本。`SELECT ... FOR UPDATE` 和 `UPDATE` 是当前读，读最新已提交的版本并加锁。可重复读下，当前读用临键锁（记录锁加间隙锁）挡住别的事务往这个范围里插行。快照读靠 Read View 看不到别人新插入后又提交的行。串行化把普通读也变成加锁读。

隔离级别在连接上设，而且要在事务开始前：

```python
engine = create_async_engine(url, isolation_level="READ COMMITTED")
```

单个连接可以用 `execution_options(isolation_level="REPEATABLE READ")`。`AUTOCOMMIT` 在 SQLAlchemy 里也是一种 isolation 选项，语句自己提交。

两笔事务按相反顺序锁两行会死锁。InnoDB 检测后回滚其中一笔，驱动抛出错。应用把这一笔整个重试，并设次数上限。锁的顺序按主键固定，能减少死锁。

事务里只留必须原子的 SQL。HTTP 和 Redis 放在事务外面。行锁会一直占到提交，外部调用一慢，就挡住别人。扣库存用一条带条件的更新，用 `rowcount` 判断有没有更新到：

```python
async with session.begin():
    result = await session.execute(
        text("UPDATE inventory SET stock = stock - 1 WHERE id = :id AND stock > 0"),
        {"id": sku_id},
    )
```

`rowcount == 0` 就是没扣成。

### SQLAlchemy

一个请求一个 `AsyncSession`，请求结束就关。会话放进全局或缓存，会把连接和对象身份串到下一次请求。

```python
async with session.begin():
    await session.execute(...)
```

`Session` 有身份映射，同一个事务里同一主键只有一份对象。提交后默认过期，下次访问会再查。异步代码里访问过期属性会触发隐式 IO。查询侧用 `expire_on_commit=False`，要新数据就显式再查。

连接池是进程级的。四个 worker 就是四份池。`pool_size` 是常驻连接，`max_overflow` 是突发时多出来的连接，总数乘以副本数要小于 `max_connections`。`pool_recycle` 小于数据库或代理的空闲断开时间。`pool_pre_ping` 在取出连接时先探活，多一次往返，只在空闲连接经常被中间设备掐掉时开。

列表接口避免 N+1。主查询之后按关联键一次 `IN` 查回来，SQLAlchemy 里用 `selectinload`。`joinedload` 用外连接，一对多时主表行会被乘开。循环里对每行 `await session.get` 会把一次列表变成几百次查询。

### 分库分表

先确认单机的索引、连接数和缓冲池已经用尽，再拆。垂直拆按业务分库。水平拆按用户 id 或订单 id 取模。

两条接入方式：

- ShardingProxy、Vitess、ProxySQL 对外说 MySQL 协议。应用拿普通连接串连代理，分片规则在代理上。跨分片事务和跨分片查询受代理能力限制。
- 应用内分片：SQLAlchemy 的 `ShardedSession` 按键选择引擎。异步会话通过 `sync_session_class=ShardedSession` 接进去。分片键必须出现在查询里，否则要扇出到所有库。

分片键选查询一定会带上的列。按用户 id 拆之后，按订单 id 查就要有一张订单 id 到用户 id 的映射，或者订单 id 里就编进用户 id。范围查询和时间排序会扇出到所有分片，这种查询改走搜索引擎。扩容用成倍的分片数，例如 8 扩到 16，数据只需要搬一半。迁移期间双写或按 id 段搬，对完数再切路由。跨分片的写入用消息做最终一致。

---

## 3. Redis

Redis 在这条体系里做四件事：缓存、计数、分布式锁、原子脚本。异步服务用 `redis.asyncio`，同步客户端会占住调用线程。应用关闭时 `aclose`。

### 命令执行

一条命令的执行在主线程上排队。快是因为数据在内存里，加上 epoll 把「连接可读」变成事件。慢命令（`KEYS`、对大 key 的 `SMEMBERS`）会堵住后面所有命令。6.0 的 IO 线程默认帮着写响应，`io-threads-do-reads` 打开后也帮着读，命令本身仍在主线程执行。

key 在服务器里是 SDS，可以存二进制，不因为里面有 `\0` 就截断。`decode_responses=True` 时响应是 `str`，适合 JSON 和数字。存压缩包或原始字节时设为 `False`。值用 JSON 或纯数字，键名带业务和版本：`order:stock:v1:{sku}`。过期时间在写入时一起设上，`SET key value EX 秒`。先 `SET` 再 `EXPIRE` 的话，两条命令之间进程挂了，键就没有过期时间。

### 结构

按过期粒度和内存选类型：

- 字符串：计数用 `INCR`，对象用 JSON。每个 key 都能单独过期。
- 哈希：一个对象的多个字段。过期只能设在整个 key 上。字段少时用紧凑编码，字段多了转哈希表。
- 列表：队列、时间线。底层是 quicklist，多个 ziplist 串成链表。
- 集合：去重。全是整数且数量不大时用 intset。
- 有序集合：排行榜。成员到分数用哈希表，按分数有序用跳表。量很小用 ziplist。
- 地理位置：`GEOADD` / `GEOSEARCH`，分数是 geohash，底层是有序集合。

哈希表冲突用链表，负载高了做渐进式 rehash：读写某个桶时顺便把旧表往新表搬，空闲也搬。客户端无感知。

管道把多条命令一次写出：`pipeline(transaction=False)`，`execute()` 拿列表。`transaction=True` 会包 `MULTI`/`EXEC`。管道里某一条失败，其他命令仍然执行，返回值里夹着异常。管道和事务都不是「先读再按结果写」的原子，读到的值在写之前可能变了。这种读改写用 Lua。`WATCH` 加上 `MULTI` 是乐观锁，写之前键变了就整事务失败，由客户端重试。

### 连接、内存与超时

`max_connections` 按这个进程的并发算。四个 worker 就是四份池，总和要小于 Redis 的 `maxclients`。`socket_connect_timeout` 管握手，`socket_timeout` 管一条命令最多等多久。超时后这条命令的结果不确定，服务器可能已经执行了 `INCR`。扣库存靠 Lua 的返回值判断，超时就用请求 id 再查一次，而不是盲目再执行。

大键用 `MEMORY USAGE` 或 `redis-cli --bigkeys` 找。删除大键用 `UNLINK`，异步释放。线上扫键用 `SCAN`。`maxmemory` 设了之后要选淘汰策略：`allkeys-lru` 在所有键里淘汰最近最少使用的，`volatile-ttl` 只淘汰设了过期时间的键，`noeviction` 在写满后拒绝写入。缓存用前两种。拒绝写入时，应用要把 Redis 故障和「键不存在」区分开，否则会把一次内存满当成缓存未命中，全部打到数据库。

### 持久化、复制、哨兵、集群

RDB 是某一时刻的全量二进制，恢复快，生成慢。`bgsave` 用 fork 和写时复制，父进程继续服务。AOF 追加命令，`everysec` 最多丢一秒。重写把一串 `INCR` 收成一条 `SET`。4.0 起混合持久化在重写时写成 RDB 头，之后的新命令仍追加 AOF。

主从第一次同步是全量 RDB，加上复制缓冲区里的增量。断线后用 backlog 里的 offset 续传，offset 已经被覆盖就退回全量。所有从节点同时找主做全量，会把主打满，所以从节点下面再挂从节点。

哨兵监视主节点。`sentinel monitor` 里的 quorum 是多少个哨兵同意才算主失效，一般是哨兵数的一半加一。客户端用 `redis.Sentinel`，`master_for` 拿当前主，`slave_for` 拿从节点。地址写死成某一台，切换之后会一直打到已经下线的主。故障切换有几秒到十几秒不可写，调用方要重试。从节点复制有延迟。扣减、锁、限购走主节点。

Cluster 把数据分成 16384 个槽，`CRC16(key) % 16384`。`RedisCluster` 收到 `MOVED` 会跳到持有该槽的节点。跨槽的多 key 命令会被拒绝，除非用哈希标签 `{sku}` 让相关 key 落在同一槽。事务、管道和 Lua 里的多个 key 也受这个限制。节点用 gossip 交换状态。集群规模控制在官方建议的范围内，不要堆到上千节点。

### 锁

单机上加锁是一条命令：`SET key token NX PX 毫秒`。token 是随机值。释放时只有值仍是自己的 token 才删除。`redis-py` 的 `Lock` 就是这样做的：获取用 `SET NX PX`，释放用 Lua 比较 token 再 `DEL`。token 放在 `threading.local`，避免线程 A 读到线程 B 后来写入的 token，把别人的锁删掉。

```python
with client.lock("sku:1001", timeout=10, blocking_timeout=2):
    ...
```

`timeout` 是锁的寿命，业务必须在这之前结束。`extend` 用 Lua 续期，同样要先对上 token。超时仍未释放，锁会消失，下一个人能拿到。这是为了进程崩溃后不会一直占着锁。业务耗时和锁超时重叠时，两个人可能都以为自己持有锁，所以超时要大于业务耗时，业务再检查一次资源状态。

主从切换时锁可能丢：写在主上，还没复制到从，主挂了，新主没有这把锁。多主投票的 Redlock 对延迟和时钟的假设很严。库存扣减用 Lua 直接改数字。锁只包住「从数据库装载库存进缓存」这种重建过程。

---

## 4. 缓存

详情页拆成能缓存的部分和必须实时的部分。标题、图、属性、规格可以缓存。价格、库存、促销单独接口，缓存时间短，或者直接读 Redis。扣库存和支付结果以数据库事务为准。

整页静态化用 Jinja2 在发布时渲染成 HTML，放到对象存储或 Nginx 能直接读的目录。模板一改要重渲染全部商品。商品一多就改成 Nginx 缓存 HTML 片段，实时数据走接口。`proxy_cache` 按 URL 缓存匿名流量。带登录态的页面按用户片段缓存，避免把一个人的页面交给下一个人。

本地缓存挡在 Redis 前面。进程内用 `cachetools.TTLCache`，设最大条数和 TTL。`functools.lru_cache` 没有 TTL，只适合启动后不变的配置。每个 worker 有自己的一份，失效不会自动广播。本地 TTL 用秒级，Redis TTL 用分钟级。

三类失效：

- 击穿：热点 key 过期，大量请求同时打到数据库。用锁只放一个请求去加载，其他请求短暂等待或返回旧值。
- 穿透：查不存在的 key。固定的空值可以缓存，TTL 更短。每次都换 key 时，用布隆过滤器先挡。Redis Stack 的 `BF.ADD` / `BF.EXISTS` 由 redis-py 调用。过滤器说不存在就一定不存在；说存在仍可能是误判，那时再查库。元素只能加不能删，删数据要换一个新过滤器再灌一遍主键。
- 雪崩：一批 key 同时过期。TTL 加随机抖动，热点 key 用锁重建。

强一致的数据留在数据库里。缓存这条链路做到最终一致：先写库，事务提交成功后再删 Redis。删失败就记一条待删记录，后台再删，TTL 是最后一道。直接改缓存里的对象要处理局部更新和并发写，容易和数据库岔开。

### 读写路径

读：先看 `TTLCache`，再看 Redis，都没有再查库，然后回填。回填用按 key 的 `asyncio.Lock`，同一个 key 同时过期时只有一个协程去查库。这把锁只挡住本进程。多个进程仍靠 Redis 锁。不要用一把全局锁。

空值也缓存。布隆过滤器放在缓存之前，元素是已经存在的主键。误判率大约是 `(1 - e^{-kn/m})^k`：`m` 是位数，`k` 是哈希次数，`n` 是预计元素个数。位数按预计数据量一次算好。超过预计容量，误判率会很快变高，查询又会漏到数据库。

缓存键带上业务版本，例如 `detail:v3:{id}`。结构变了就换前缀，旧键等 TTL 自己消失。展示用的总库存、总条数可以短暂不准，放 Redis 并加随机 TTL。

---

## 5. Elasticsearch

搜索走 Elasticsearch，下单和库存走 MySQL。倒排索引按词指向文档 id。中文用 IK：`ik_max_word` 切得细，`ik_smart` 切得粗。7.x 起类型固定为 `_doc`。

商品索引：标题、关键词、副标题用 `text` 加 IK；品牌名、类目名、图片 URL 用 `keyword`；价格、销量、上架时间、是否有货用数值、日期、布尔。属性是一对多，映射成 `nested`（`attrId`、`attrName`、`attrValue`）。普通 object 数组会被拍扁，`颜色=黑` 和 `尺寸=L` 会错配成交叉组合。

异步服务用 `AsyncElasticsearch`，进程退出时 `await client.close()`。条件多时用 DSL：`AsyncSearch(using=client, index="product_db")`，`s.query(...)`，`to_dict()` 看最终 JSON。已经有查询字典时，直接 `client.search`。

品牌和属性筛选项是聚合。bucket 是分组，metric 是组上的统计。

```python
s.aggs.bucket("brands", aggs.Terms(field="brandName", size=50))
s.aggs.bucket("attrs", aggs.Nested(path="attrs")).bucket(
    "names", aggs.Terms(field="attrs.attrName")
)
```

nested 属性要先进入 nested 文档再 terms，否则桶会把不同属性的值交叉在一起。高亮用 `highlight`，`fragment_size` 控制片段长度，`number_of_fragments` 控制返回几段。

### 查询、写入、索引

筛选放进 `bool.filter`：品牌、价格区间、有货、类目。`filter` 不参与打分，可以被缓存。关键词放进 `bool.must` 的 `multi_match`，字段用 `name^3`、`keywords^2`、`subTitle`。`should` 用来加分，例如品牌名刚好等于输入词。

用户选了价格或销量，就按那个字段排。默认排序用相关性，也可以用 `function_score` 把销量、上架时间折进分数。分页用 `search_after`：排序字段里带一个唯一值，下一页把上一页最后一条的 `sort` 传回去。`from + size` 超过 `index.max_result_window`（默认 10000）会被拒绝。

商品保存成功后发消息，消费者写 ES，失败可重试。搜索允许秒级延迟。写入用 `helpers.async_bulk`。429 和 503 退避重试，映射错误进失败队列。更新用 `if_seq_no` 和 `if_primary_term`：序号不一致就重新读再写。

默认 `refresh_interval` 是 1 秒，写完立刻搜可能还看不到。主分片数在索引建好后要改，就建新索引再切别名。查询可以打在副本上，写入打在主分片再同步。别名指到当前索引，对完数再原子拨过去。

分词链是字符过滤、切词、token 过滤。要去 HTML、替换字符、丢掉停用词，就在 settings 里挂 custom analyzer，并同时设 `search_analyzer`。两边不一致时，入库的词和查询的词对不上。改分词器或已有字段的类型都要重建索引。

Master 管元数据和分片分配，Data 节点存分片并承担读写。应用连接超时设短，搜索读超时按慢查询上限设。聚合限制 `size` 和 `timeout`。客户端超时取消不了已经在节点上跑的查询。

---

## 6. 消息队列

队列用来解耦、异步和削峰。生产者写入后即可返回，消费者按自己的速度处理。下游停了，消息还在；队列自己停了，这条链路停。

本系统稍后执行的任务用 Celery 或 RQ。要和别的服务交换业务事件，用 RabbitMQ、RocketMQ 或 Kafka，正文用 JSON。

默认是至少投递一次。超时后生产者会重发，消费者处理完却没确认，消息也会再来。消费逻辑按唯一业务键做成幂等。恰好一次依赖幂等生产者加上只提交已处理位点，范围很窄，业务侧仍按幂等来写。

本地事务和发消息放不到同一个数据库事务里。发件箱把这件事留在自己的库里：业务事务同时写入订单和一张 `outbox` 表，字段有消息 id、topic、正文、状态。提交之后，另一个循环把未发送的行推到队列，成功再标成已发送。进程在推送前停了，重启后会再推，所以消费者仍要幂等。RocketMQ 的事务消息是 Broker 先收下一条消费者看不见的 half 消息，等本地事务结果再提交或回滚。发件箱不依赖某一种 Broker 的事务能力。

Celery 适合发邮件、生成报表、可以重试的第三方调用。`task_acks_late=True` 让任务做完再确认，worker 中途被杀会重跑。broker 用 Redis 时，堆积体现在列表长度上，没有交换机和死信这套路由。跨服务事件用队列的 topic，不用 Celery 的任务名。

### RabbitMQ

生产者只对交换机发，消费者只对队列收。队列没绑定交换机就收不到消息。异步服务用 `aio-pika`，`connect_robust` 负责断线重连。处理完再 ack。失败则 nack，并决定要不要重新入队。

四种交换机。扇出复制到每一个绑定的队列。直连按完整的 routing key 匹配。主题用 `*` 匹配一个词、`#` 匹配零个或多个词。头交换机按消息头匹配。

publisher confirm 打开之后，Broker 落盘（或按策略写入）才给回执。没开 confirm 时，`publish` 返回只说明写进了客户端缓冲区。队列不存在且设置了 mandatory 时，走 return 回调，发件箱那一行保持未发送。

死信有三条来源：队列满了并且拒绝新消息、消费者拒绝且 `requeue=False`、消息超过 TTL。队列参数 `x-dead-letter-exchange` 和 `x-dead-letter-routing-key` 指向下一跳。用「每条消息一个 TTL + 死信」做延迟时，队列按队头检查过期，队头没到期，后面已经到期的消息也出不去。延迟各不相同就用延迟插件，或按延迟档位拆队列。

队列用仲裁队列（quorum queue），副本按多数派确认。prefetch 是每个消费者一次拿多少条未 ack 的消息。设得太大，消息堆在一个慢进程里，别的进程空闲。积压时先加消费者。幂等用数据库唯一键或 Redis `SET NX`。

### RocketMQ

5.x 的 Python 客户端是 `rocketmq-python-client`，走 gRPC，仓库是 `apache/rocketmq-clients`。客户端连 Proxy 端点，由 Proxy 找 Broker。NameServer 在 5.x 部署里仍然保存 Broker 信息。

Producer 往 topic 发，Consumer 按组拉。组里的消费者分摊消息。广播是组里每个消费者都收到全量。消息上有 topic、tag、keys、body。tag 用来在订阅时过滤，keys 用来按业务 id 查。

`SimpleConsumer.receive(max_num, invisible_seconds)` 拉到的消息在不可见时间内不会再给别人。处理完必须 `ack`。超过不可见时间还没 ack，消息重新可见。失败让它超时再投，多次失败进重试 topic，超过次数进死信 topic。死信要有人消费或告警。消费后的消息按 Broker 上的保留时间删除，例如每天固定时刻删掉超过保留小时数的文件。

同一笔订单的创建、支付、取消用同一个消息组，Broker 把它们放进同一个队列，消费者按队列顺序处理。顺序只在一个队列里成立。扩容之后，新的组才会落到新队列。

事务消息：先发 half 消息，消费者看不见；执行本地事务；再提交或回滚。进程在提交前停了，由回查决定。回查做成无副作用的查询：本地事务表里有记录就提交，没有就回滚。

### Kafka

消息按 topic 再按 partition 追加，消费后不删，靠保留时间或大小清理。消费者属于一个组。同组一条消息只给一个消费者。另一组可以再消费全量。offset 提交了，同组就不会再投这条。

客户端用 `confluent-kafka`（librdkafka）或 `aiokafka`。处理成功再提交位点。自动提交等于拉到就算消费完，处理到一半进程停了就会跳过这条。两次 poll 的间隔有上限，处理太慢会被踢出组，分区分给别人。一次 poll 的条数按处理速度设。

新组从哪开始由 `auto.offset.reset` 决定。`latest` 只消费启动之后的新消息。`earliest` 从保留范围内最早的一条开始，之后仍接上次提交的位点。这和每次手动 seek 到开头不同。换 `group.id` 等于换一组新进度。

Rebalance 在组成员变化或分区数变化时发生。Range 按分区段切，Round-robin 轮询，Sticky 尽量保住上次的分配，只挪走挂掉成员的分区。协调器是该组 offset 所在 `__consumer_offsets` 分区的 leader。再平衡期间这组成员暂停消费。

消息 key 决定分区。同一订单号进同一分区，这个分区里才有顺序。key 太集中时，流量堆在一个分区上，加消费者也没用，因为一个分区同时只被组里的一个消费者读取。消费者数量超过分区数时，多出来的一直空闲。要提高并行度就先增加分区。

生产者 `acks=all`，主题的 `min.insync.replicas` 至少为 2，一条消息要落到足够的副本才算成功。`enable.idempotence` 让生产者重试时不写出重复序号。这覆盖的是生产者自己的重试。消费者处理成功但提交位点失败，仍会再投一次。

积压看 lag：分区末端位点减去已提交位点。先分辨是处理变慢还是生产变快，再决定加分区还是改处理函数。扩容消费者之前先确认还有空闲分区。

---

## 7. 秒杀

普通购买是查商品、建单、扣库存、付款、发货。秒杀是活动页、详情、立即抢、去付款。特点是价格低、瞬时并发高、限时限量、要预热（今天的页面上放明天的商品）。

读和写分开。详情走缓存或静态页。写的本质是并发扣减，要保证不超卖。秒杀接口单独路由、单独副本，限流放在网关上，超过的返回排队页。

活动开始前把库存写进 Redis。扣减是一段 Lua，多条命令之间不会被别的客户端插进来。售罄标记如果只放在某个 worker 的内存里，别的进程看不到。标记的来源是 Redis：Lua 返回售罄，或者订阅一个售罄频道。Pub/Sub 不留存，进程刚启动时本地标记是空的，第一批请求打到 Redis，再把标记树起来。

```python
DECR_IF_POSITIVE = """
local left = tonumber(redis.call('GET', KEYS[1]) or '-1')
if left <= 0 then
  return -1
end
return redis.call('DECR', KEYS[1])
"""
```

限购和扣减放进同一段 Lua，避免扣完又发现这个用户已经买过，还得把库存加回去：

1. 请求 id 已有结果，直接返回上次的结果。
2. `SET 用户限购键 1 NX EX 活动时长`。设不上就是已经买过。
3. 库存大于 0 才 `DECR`，否则删掉限购键并返回售罄。
4. 把用户、SKU、请求 id 放进队列。队列满了就 `INCR` 还库存、删限购键，接口返回稍后再试。

消费者按数据库能承受的速度建单。订单表的唯一键是用户 id + 活动 id + SKU，或请求 id。插入成功再写扣减流水。碰到唯一键就是重复投递，确认消息即可。数据库故障则不确认，稍后再消费。多次仍失败就把库存还回 Redis，并记入失败表。

定时任务用「预热库存 − 成功订单数」和 Redis 当前值比较，偏差超过阈值再告警。活动结束后以订单表为准做一次对账。

热点 SKU 的一个 key 会打在集群的一个槽上。库存拆成 `stock:{sku}:0` 到 `stock:{sku}:9`，请求随机选一段，这一段空了再试别的段。哈希标签 `{sku}` 让这些键落在同一个槽，一段 Lua 里可以连续尝试。拆段之后总库存是各段之和，展示用短缓存。

有人绕过接口直接改库时，缓存不会跟着变。订阅 binlog，而不是轮询表。`mysql-replication` 的 `BinLogStreamReader` 扮成 slave。MySQL 8 要 `binlog_format=ROW`、`binlog_row_image=FULL`、`binlog_row_metadata=FULL`，账号有复制权限，`server_id` 不和现有副本冲突。位点要自己存，断线从位点续上。也可以让独立的 Canal 进程拉 binlog 再推进 Kafka，应用只做消费者。解析到库存或价格变更后删缓存，或发消息更新搜索索引。这件事放在订阅进程里，不放在下单请求里。

---

## 8. 服务拆分与调用

中台是把各业务线里重复的能力抽出来。业务中台是用户、会员、支付、权限这种各条线都要的服务。数据中台是统一的存储、建模和计算。技术中台是日志、配置、鉴权、任务这些共用平台。

烟囱是每条产品线各做一份权限和订单，共性抽不出来。落地顺序是：先按业务拆开，再把共有能力收成平台，最后调整编制和投入。系统不够大时，抽中台的协调成本会高于留在一个仓库里。

代码上对应三种边界：

- 一个进程里的包：`accounts`、`orders` 只是目录，调用是函数调用。
- 独立进程：各自的镜像、Deployment、数据库。调用走 HTTP 或消息。一个进程停了，另一个进程还在。
- 内部包：发到内部索引，各服务钉死主版本。包里放日志格式、追踪上下文、错误码。创建订单这种业务留在订单服务里。

对外提供完整能力。调用方拿用户 id 就能完成登录态校验。

服务之间用一个进程级的 `httpx.AsyncClient`，启动时创建，关闭时 `aclose`。每次请求新建客户端会反复握手，连接池也攒不起来。超时拆开：连接超时短，读超时按这个接口的正常耗时加余量。重试只针对幂等的 GET，以及带了幂等键的 POST。没有幂等键的下单，超时后先查结果，再决定要不要重发。

熔断是连续失败后暂时不再调用，给对方恢复的时间，也避免自己的连接全堵在一个已经坏的下游上。半开时放少量探测。打开期间返回缓存里的旧数据，或一个明确的稍后再试。降级路径里不再同步调用同一个下游。

---

## 9. 网关、配置与发现

鉴权、限流、黑名单放在入口，每个服务就不用各写一份。APISIX 或 Kong 放在 Pod 前面：

- 路由：按路径或 Host 转到哪个上游。上游是一组 Pod 的 Service。
- 匹配：路径前缀、Header、方法。不满足就不转发。
- 插件：JWT、限流、改写路径、请求 id、访问日志。

只有一个服务、流量也不大时，用 FastAPI 的 `Depends` 在进程内做鉴权。服务多了之后，令牌校验放到网关，后面的服务信任网关注入的用户 id。这个头在最外层入口清掉再由网关写入，调用方自己带的同名头进不了内网。

网关做所有服务都要的事：TLS、限流、鉴权、请求 id、访问日志、转发。进程里留和这个资源有关的事：参数校验、这笔订单是不是这个用户的、幂等。网关不知道订单属主。

`Depends` 挂在路由上，只包这一条接口。中间件包住所有请求，适合请求 id 和访问日志，并且要接住异常，否则 500 没有统一格式。请求 id 从网关的头读进来，没有就自己生成，放进 `contextvars`，日志和下游调用都带上。

超时从外往里缩短。客户端 3 秒，网关 2.5 秒，服务调下游 2 秒。外层更长，内层的错误才能返回。网关先断开时，服务可能还在写数据库，调用方重试就会重复下单。

限流按路由配。秒杀和普通查询分开。固定窗口会在窗口边界放进两倍流量。漏桶或令牌桶把流量抹平。限流键用用户 id，没有登录态时用 IP。只按 IP 会误伤出口地址相同的一群人。

### Nacos

Nacos 同时做注册和配置。纯 Kubernetes 用 Service 和 CoreDNS 发现，配置用 ConfigMap。虚拟机或混部再用 Nacos。两套发现会给出不同的地址，选一套。

Python 用 `nacos-sdk-python` v2，走 gRPC，端口是 HTTP 端口加 1000。`ClientConfigBuilder` 里写地址、命名空间、账号。

```python
config_client = await NacosConfigService.create_config_service(client_config)
raw = await config_client.get_config(
    ConfigParam(data_id="order-dev.yaml", group="DEFAULT_GROUP")
)
```

Data Id 用「应用名-环境.后缀」。共享配置和扩展配置分成不同 Data Id，后读到的覆盖先读到的。命名空间或分组写错时读到空内容。`add_listener` 在变更时回调。回调里替换进程内的配置对象。已经建立的数据库连接池不会因为配置文本变了就自动换主机，换地址要重建引擎。

回调不一定跑在事件循环线程上。回调里只解析文本、换上一个不可变配置对象。要通知协程就 `call_soon_threadsafe`。解析失败保持旧配置。

本地缓存目录在连不上服务器时用来启动。容器根文件系统只读时，把这个目录指到可写的 emptyDir。

```python
naming = await NacosNamingService.create_naming_service(client_config)
await naming.register_instance(RegisterInstanceParam(
    service_name="order",
    ip=ip,
    port=8000,
    group_name="DEFAULT_GROUP",
))
```

调用方 `list_instances(..., healthy_only=True)`，在健康实例里按权重选。SDK 负责心跳和本地缓存。进程退出要反注册。注册用临时实例：心跳在，实例就在；心跳没了，服务器超时后摘掉。持久实例没有心跳，崩溃后会一直留在列表里。调用方使用订阅来的列表。

---

## 10. 镜像与编排

### Docker

基础镜像用 `python:3.12-slim`，标签钉死版本。多阶段构建：第一阶段安装依赖，第二阶段只保留虚拟环境和代码。`PYTHONDONTWRITEBYTECODE=1` 不写 `.pyc`，`PYTHONUNBUFFERED=1` 让日志立刻进入 Docker 的日志驱动。锁文件放进构建上下文，镜像里按锁安装。

用非 root 用户运行。`USER` 放在 `ENTRYPOINT` 前面。`COPY` 拷文件。`EXPOSE` 只是文档，映射靠 `-p` 或 Compose 的 `ports`。

启动命令用 exec 形式，让 Uvicorn 成为 PID 1，才能收到 SIGTERM：

```dockerfile
ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

shell 形式会多一层 shell，停容器时信号打在 shell 上，worker 收不到，最后被强杀。内存用进程自己的指标或 `tracemalloc` 看。

依赖很少变，代码经常变，所以先安装锁文件，最后才 `COPY` 源码。

```dockerfile
FROM python:3.12-slim AS build
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
WORKDIR /app
RUN useradd --create-home --shell /usr/sbin/nologin app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`.dockerignore` 去掉 `.git`、`.venv`、`__pycache__`、测试数据和本地 `.env`。上下文里带了 `.venv`，会把宿主机的二进制打进去。

日志打到 stdout。需要留存的上传文件用命名 volume，目录对用户 `app` 可写。只读配置挂 `:ro`。

Compose 把应用、MySQL、Redis、队列写在一份 YAML 里。服务之间用服务名当主机名，例如 `redis:6379`。`depends_on` 只保证启动顺序。数据库和 Redis 写 `healthcheck`，应用用 `condition: service_healthy`。健康检查通过只说明端口开了，迁移是否跑完由应用自己重试。`expose` 只在 Compose 网络里可见，`ports` 才映射到宿主机。`links` 是遗留字段。

镜像标签用 git 提交号。`docker build -t 账号/app:版本 .` 之后 `docker push`。回滚时指回旧标签。本地 Compose 和生产清单共用镜像名、端口、环境变量名。副本数和资源限制各自写。生产不用 `network_mode: host`，否则绕开 Service 的负载均衡。

### Kubernetes

Kubernetes 负责调度、自愈、Service 负载均衡、滚动升级、HPA、ConfigMap 和 Secret。Python 服务要额外处理启动慢，以及多进程带来的内存。

导入 FastAPI、ORM 和搜索客户端要几秒。`startupProbe` 给足失败次数，过了启动阶段才开始存活探针。就绪探针失败只是从 Service 摘掉，不重启。存活探针打 `/healthz`，只判断进程还能不能回答。就绪探针打 `/readyz`，确认连得上 Redis 和数据库。存活探针不去查数据库。数据库一抖动，kubelet 会把 Pod 全部重启。

```yaml
startupProbe:
  httpGet: { path: /healthz, port: 8000 }
  periodSeconds: 5
  failureThreshold: 30
readinessProbe:
  httpGet: { path: /readyz, port: 8000 }
  periodSeconds: 5
livenessProbe:
  httpGet: { path: /healthz, port: 8000 }
  periodSeconds: 10
```

内存按副本数乘以每个 Pod 里的 worker 数估算，写进 `resources.requests` 和 `limits`。CPU 的 limit 会触发节流，延迟变高，CPU 指标不一定很高。IO 型服务把 CPU limit 设得明显高于 request。内存 limit 仍然要设。超内存是杀进程，超 CPU 是变慢。

HPA 看 CPU 时，进程经常在等 IO，CPU 很低但连接已经满了。更有用的是正在处理的请求数、事件循环滞后、连接池占用。没有这些指标时，先按内存和固定副本数扩。

Service 有四种。`ClusterIP` 只在集群内。`NodePort` 用节点 IP 加固定高位端口。`LoadBalancer` 在云上给外部 IP。`ExternalName` 返回 CNAME。应用只连 Service 的 DNS 名。Deployment 的 `selector` 和 Pod 模板标签一致，副本数写在 `replicas`。清单的一级字段是 `apiVersion`、`kind`、`metadata`、`spec`、`status`。

滚动更新用 `maxUnavailable: 0`、`maxSurge: 1`：先起一个新 Pod，就绪之后再停一个旧的。`minReadySeconds` 设几秒，避免刚通过探针就接全量流量。`PodDisruptionBudget` 的 `minAvailable` 保证腾挪节点时至少留一个能服务的 Pod。

`preStop` 里先等几秒，让端点从 Service 摘掉，再让进程处理 SIGTERM。这段时间小于 `terminationGracePeriodSeconds`。

配置进 ConfigMap，密钥进 Secret。密钥不写进镜像层。只改 ConfigMap 不会重启容器，进程已经读进 `os.environ` 的值也不会变。需要整进程重载时，给 Pod 模板加一个配置摘要注解，摘要变了就滚动。

---

## 依据

- [CPython free-threading](https://docs.python.org/3/howto/free-threading-python.html)，[asyncio 任务](https://docs.python.org/3/library/asyncio-task.html)
- [Uvicorn 部署](https://uvicorn.dev/deployment/)，[Gunicorn 进程模型](https://gunicorn.org/design/)
- [SQLAlchemy 2.0 连接与隔离级别](https://docs.sqlalchemy.org/en/20/core/engines_connections.html)
- [redis-py Lock](https://github.com/redis/redis-py/blob/master/redis/lock.py)，[Redis 分布式锁](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/)
- [elasticsearch-py DSL](https://www.elastic.co/docs/reference/elasticsearch/clients/python/dsl_how_to_guides)
- [RabbitMQ 仲裁队列](https://www.rabbitmq.com/docs/quorum-queues)，[Kafka 投递语义](https://kafka.apache.org/documentation/#semantics)
- [RocketMQ 客户端](https://github.com/apache/rocketmq-clients)，包名 `rocketmq-python-client`
- [Nacos Python SDK](https://github.com/nacos-group/nacos-sdk-python)，[使用说明](https://nacos.io/docs/v2.5/manual/user/python-sdk/usage/)
- [python-mysql-replication](https://github.com/julien-duponchelle/python-mysql-replication)

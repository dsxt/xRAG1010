# 创业公司Web3 Golang 后端拿到offer但犹豫去不去

> 根据面试截图整理。题干来自原帖，参考答案用于复习，不是候选人当时的口述。

## 一面

**现场记录**

拿到 offer 了，但还在犹豫去不去。Web3 现在风险比较大，给的钱比大厂高 30%。公司不大，20 来人，技术栈比较新。CTO 面得比较细，问了很多项目上的实际问题。CEO 面聊得比较随意，主要看价值观合不合。这一轮是 CTO 视频面，大约 45 分钟。

### 1. Go 的 goroutine 和线程有什么区别？

**参考答案**

goroutine 是 Go 运行时调度的用户态协程，线程是操作系统内核线程。goroutine 的创建、栈和切换都更轻。

goroutine 起始栈大约 2KB，不够就按大约两倍扩容，空闲再收缩。线程栈通常是 MB 级，创建还要进内核。一个进程里可以挂很多 goroutine；线程数量上去之后，内存和切换成本先到顶。真正同时执行 Go 代码的线程数由 `GOMAXPROCS` 决定。Go 1.25 起，Linux 上的默认值取可用逻辑 CPU 和 cgroup CPU limit 里较小的那个，参与计算的是 limit。限额先向上取整，小于 2 的限额会提到 2，除非机器的逻辑 CPU 更少。手动设置 `GOMAXPROCS` 之后，这套自动调整停止。从 Go 1.14 起，长时间不让出的 goroutine 会被信号异步抢占。

网络等待只把 goroutine 挂到 netpoller，线程继续跑别的任务。进入阻塞系统调用时，这个线程会暂时离开逻辑处理器，运行时再找线程来跑。协程之间常用 channel 交接数据。泄漏的 goroutine 会占着栈，栈又是 GC 的根，所以要看 goroutine 数量，不能只看线程数。

### 2. Go 的 GMP 模型了解吗？调度器工作流程？

**参考答案**

G 是 goroutine，M 是操作系统线程，P 是逻辑处理器。M 必须绑上 P 才能执行 Go 代码。P 的数量就是 `GOMAXPROCS`，它决定并行度；G 的数量可以远大于 P。

每个 P 有一条长度 256 的本地运行队列，还有一个 `runnext` 槽，用来接着跑刚被唤醒的 G。本地队列满了，会把大约一半挪到全局队列。调度时大约每 61 次先看一眼全局队列，避免堆在全局队列里的 G 饿死；其余时候先取 `runnext` 和本地队列。本地和全局都空，就做一次非阻塞的 netpoll，再从别的 P 偷大约一半本地队列。

网络阻塞只占用一个 G，M 不退出。G 进入阻塞系统调用时，M 和 P 分离，P 交给别的 M，系统调用就不会把逻辑处理器卡死。调用返回后，这个 G 再去抢 P，抢不到就进全局队列。sysmon 不占 P，会把运行大约超过 10ms 的 G 抢占，也会把长时间停在系统调用里的 P 抢走。

P 的数量是并行度，G 的数量是并发度。容器里把 `GOMAXPROCS` 调得高于 CPU limit，只会增加抢占和被 cgroup 限流的机会。

### 3. Channel 底层原理是什么？有缓冲和无缓冲有什么区别？

**参考答案**

channel 在运行时是一把锁保护的 `hchan`：环形缓冲区、发送下标、接收下标、发送等待队列、接收等待队列，再加上关闭标志。等待者用 sudog 挂在队列上。

无缓冲 channel 的缓冲区长度是 0。发送和接收必须相遇，数据从发送方直接拷到接收方。有缓冲 channel 把元素放进环形缓冲区：缓冲没满，发送不阻塞；缓冲不空，接收不阻塞。缓冲满了发送才阻塞，空了接收才阻塞。

已经有接收者在等时，缓冲区一定是空的，这次发送会直接交给接收者。缓冲区满了并且有发送者在等时，接收方拿走的是缓冲区里最老的元素，等待发送者的值补进缓冲区，顺序仍然是先进先出。

关闭只能由发送方做。关闭 nil channel、重复关闭、向已关闭 channel 发送，都会 panic。对已关闭 channel 接收，会先把缓冲取完，然后立刻得到零值和 `false`。向 nil channel 发送或接收会永远阻塞。`select` 里几个 case 同时就绪就均匀随机挑一个；把某个 case 的 channel 设成 nil，这个分支就不会再被选中。

### 4. Context 怎么用？什么场景下用？

**参考答案**

`context.Context` 沿调用链传递取消、截止时间和请求范围的数据。取消是协作的：父 context 取消后，由它派生的子 context 也会取消，但已经在跑的函数必须自己看 `Done`，运行时不会把 goroutine 杀掉。

函数第一个参数传 `ctx`，在出站调用、数据库和 Redis 上带上同一个 `ctx` 或更短的超时。`WithTimeout`、`WithDeadline` 用于这次请求最多做多久；`WithCancel` 用于主动停掉一组子任务，函数返回前要 `cancel`，避免派生的 goroutine 泄漏。`WithValue` 只放请求级的数据，例如 trace id；key 用未导出的类型，避免和别的包撞键。业务上的可选参数走函数签名，不塞进 Value。

HTTP 入口、gRPC、工人任务、访问节点和数据库，都是该用它的地方。父请求已经结束、子任务还要单独跑完时，用 Go 1.21 的 `WithoutCancel` 切断取消，但这个子任务必须另有退出条件。context 不要存进长期存活的结构体里再拿来用，取消信号会和那次请求的生命周期拧在一起。

### 5. Go 内存管理、GC 机制了解多少？

**参考答案**

堆分配类似 tcmalloc。每个 P 有自己的 mcache，按大小规格取小对象，常见路径不加全局锁。规格不够时向 mcentral 要 span，再往下是 mheap，页大小 8KB。小于 16 字节且不含指针的对象走 tiny allocator，几个挤在一个 16 字节块里。大于 32KB 的对象单独拿连续页。goroutine 的栈不走这套堆分配，栈扩容是拷贝。

GC 是并发、精确、不分代、不整理的标记清除。白色还没访问，灰色已经可达但子指针没扫完，黑色的子指针已经扫完。从栈和全局变量这些根出发，标记结束仍是白色的对象不可达。标记期间新分配的对象直接标成黑色，这一轮不会被收掉。Go 1.8 起的混合写屏障会把被覆盖的旧指针涂灰；当前栈还没扫完时，新写入的指针也涂灰。栈扫过一次就当作黑色，所以 STW 主要留在标记开始和标记结束。清扫是懒的，分配时顺手扫，后台也会扫。分配太快、后台标记跟不上时，业务 goroutine 会被拉去协助标记。

`GOGC` 默认 100。下一轮的堆目标大约是上一轮存活堆，再加上「存活堆 + GC 根」乘上 `GOGC/100`；根里包含 goroutine 栈。多数服务存活堆远大于根，看起来接近堆涨一倍再回收。Go 1.19 起可以用 `GOMEMLIMIT` 设软上限，上限贴着存活集会让 GC 一直转。Go 1.26 起默认的 Green Tea 仍是这套三色标记和写屏障，变化是把同一页上待扫描的小对象攒在一起扫，减少在堆里来回跳。GC 结束不等于内存立刻还给操作系统，归还由 scavenger 慢慢做，所以 RSS 不会马上掉下去。

### 6. Redis 在项目中怎么用的？什么场景？

**答题思路**

按「场景、数据结构、一致性、失败了怎么办」讲你真正用过的那几个，每个一两句。

1. 缓存用 Cache-Aside。读先看 Redis，没有再查数据库并回填。更新先写数据库，再删缓存。过期时间加随机抖动。热点 key 用单飞重建，避免同时打穿数据库。
2. 计数、限流、排行榜：计数用字符串或哈希，排行榜用有序集合。说清过期时间，以及谁在计数和数据库不一致时做校正。
3. 分布式锁、会话、用 Stream 或 List 做队列：用过再讲。锁要讲唯一 token 和过期；队列要讲进程崩溃时消息还在不在。

余额、nonce、链上头寸以数据库或链上状态为准，Redis 里放可以重建的投影和短期锁。缓存和库会短暂不一致，把你能接受的窗口说出来。没用过的结构不往上加，对方会追问一条命令失败时你怎么处理。

### 7. MySQL 索引优化，EXPLAIN 怎么分析？

**参考答案**

先用 EXPLAIN 看优化器打算怎么访问，再决定加哪一个索引。InnoDB 的二级索引叶子上存的是主键，没被索引覆盖的列还要回表。

重点看四列。`type` 从 `const`、`eq_ref`、`ref`、`range` 到 `index`、`ALL`；`ALL` 是扫整表，`index` 是扫整棵索引。`key` 是真正用上的索引，`possible_keys` 只是候选人。`key_len` 可以看出联合索引用了多长的前缀，字符集会影响字节数。`rows` 和 `filtered` 都是估算。Extra 里 `Using index` 表示覆盖索引，`Using index condition` 是索引下推，`Using filesort` 是额外排序，`Using temporary` 是临时表。MySQL 8.0.18 起用 `EXPLAIN ANALYZE` 看实际耗时和行数，计划树用 `FORMAT=TREE`。

对着这些列改索引：

1. `type` 是 `ALL` 且行数大：列上没有可用索引，或者被函数、隐式类型转换、前导模糊匹配挡住了。
2. 联合索引遵守最左前缀。等值列放前面，范围列放后面。范围条件后面的列不能继续用来定位，但索引下推仍可能用它减少回表。
3. 查询列都在索引里时出现 `Using index`，这次就没有回表。
4. `ORDER BY`、`GROUP BY` 和索引顺序一致时，可以去掉 filesort。
5. 每加一个二级索引，写入都要维护它。先看慢日志和 EXPLAIN，再加最窄的那一个。

统计信息过旧时优化器会选错索引，`ANALYZE TABLE` 之后再看一次计划。

### 8. 你为什么从上一家公司离职？

**答题思路**

按「事实、收尾、下一段」说，一分钟内说完。

1. 事实只留一个真实原因：业务方向变了、负责的系统进入维护，或者你想做的技术在那里没有空间。
2. 收尾讲交接：已经做完，或者还要多久。人、制度和薪酬放在别的问题里再答。
3. 下一段对准这次机会：想做 Go 后端里和链上数据、交易或钱包相关的系统，小团队能碰到从节点到接口的整条链路。
4. 原因说得具体，对方就不好把话题带回情绪。记不清的时间线用你能确认的月份。

### 9. 你对 Web3 有什么了解？

**参考答案**

工程上的 Web3，是用户自己保管私钥，用签名授权链上合约改变状态。应用服务器不代替用户持有资产。

一条链是一组节点复制同一份账本。区块用前一个区块的哈希串起来。账户余额、合约代码和合约存储记在状态树里。谁有私钥，谁就能动对应地址里的资产。读状态是向节点发 `eth_call`，不上链、不付 gas。写状态要签名一笔交易，付 gas，等区块打包，回执成功才算执行完成。

和普通后端相比，多了三件要处理的事。节点会重组，刚看到的收据可能消失。写入又慢又贵，查询不能直接扫链，要靠索引日志。私钥泄漏就是资金丢失，热钱包不能放在对外的 API 进程里。

Go 后端常见的位置是索引、询价、拼交易、风控和对账。合约仍是结算的依据，数据库里的余额只是投影。

### 10. 实现一个 worker pool，控制并发数（15 分钟）

**参考答案**

固定 N 个工人 goroutine 消费一个任务队列，N 就是并发上限。队列长度用来堆积和反压，不增加同时执行的任务数。

`Close` 关掉队列并等待工人把已入队的任务做完。任务自己的 context 负责取消业务，池子不会杀掉正在跑的任务。工人里接住 panic，否则一个任务会让工人数量变少。`Submit` 和 `Close` 用同一把锁保护「已关闭」和 `close(channel)`，避免向已关闭的 channel 发送。队列满时 `Submit` 会占着这把锁直到入队或 ctx 取消，所以任务里面不要再同步等待同一个池子，否则工人全部堵在提交上，谁也腾不出手来取任务。

入队是 O(1)，空间是 O(工人数 + 队列长度)。如果只要限制并发、不要求复用工人，`golang.org/x/sync/errgroup` 的 `SetLimit` 就是一个信号量；题目要的是工人池，所以用固定数量的 goroutine。

```go
package pool

import (
	"context"
	"errors"
	"sync"
)

var ErrClosed = errors.New("worker pool closed")

type Pool struct {
	jobs chan func()
	wg   sync.WaitGroup

	mu     sync.Mutex
	closed bool
}

func New(workers, queue int) *Pool {
	if workers < 1 {
		panic("workers < 1")
	}
	if queue < 0 {
		queue = 0
	}
	p := &Pool{jobs: make(chan func(), queue)}
	p.wg.Add(workers)
	for i := 0; i < workers; i++ {
		go p.worker()
	}
	return p
}

func (p *Pool) worker() {
	defer p.wg.Done()
	for job := range p.jobs {
		func() {
			defer func() { recover() }()
			job()
		}()
	}
}

func (p *Pool) Submit(ctx context.Context, job func()) error {
	if job == nil {
		return errors.New("nil job")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.closed {
		return ErrClosed
	}
	select {
	case <-ctx.Done():
		return ctx.Err()
	case p.jobs <- job:
		return nil
	}
}

func (p *Pool) Close() {
	p.mu.Lock()
	if !p.closed {
		p.closed = true
		close(p.jobs)
	}
	p.mu.Unlock()
	p.wg.Wait()
}
```

## 二面

**现场记录**

技术面，现场，大约 1 小时，题目都是实战。

### 1. 你说做了高并发项目，QPS 多少？峰值多少？

**答题思路**

先把口径说死，再给数字。数字用监控或压测里的真实值。

1. 哪个入口：网关总 QPS，还是下单、查询这一个接口。成功请求和全部请求分开。
2. 峰值的窗口：每秒最大，还是一分钟平均。发生在什么时候，当时几台实例。
3. 同时给延迟，至少有 p99。只有 QPS、没有延迟，说明不了系统是否已经顶住。
4. 峰值从哪来：行情、活动、定时任务，还是重试风暴。瓶颈在 CPU、锁、数据库还是下游，当时做了什么。
5. 压测数字和线上数字分开说。记不清就说指标在哪张图上，不现场凑一个整数。

### 2. 分布式锁怎么实现的？Redisson 了解吗？

**参考答案**

单 Redis 上的互斥，用一条 `SET key token NX PX ttl`。token 是这次加锁随机生成的值。解锁用 Lua：先比较 token，是自己的才 `DEL`。直接 `DEL` 会把别人刚拿到的锁删掉。

```go
ok, err := rdb.SetNX(ctx, key, token, ttl).Result()

const unlock = `
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
else
  return 0
end`
rdb.Eval(ctx, unlock, []string{key}, token)
```

业务可能长过 TTL，就在持有期间续期：Lua 确认 token 还是自己，再延长过期。续期失败就停止这次业务。进程崩溃后看门狗停了，锁会在剩余 TTL 后释放。

Redisson 是 Java 客户端。它的锁默认可重入，Redis 里用 hash，field 是客户端和线程，value 是重入次数。不指定 `leaseTime` 时看门狗生效：默认 `lockWatchdogTimeout` 30 秒，大约每三分之一这个时间续一次，把 TTL 重新设成 30 秒。传入了 `leaseTime` 就不会续期，业务没跑完锁也会掉。等待方靠 pub/sub 被唤醒。Go 里对应的是 go-redis 加上面的脚本；`redsync` 实现的是多主 Redlock。

主从异步复制时，锁可能在故障转移后丢。Redlock 还依赖时钟和网络假设。锁丢掉之后业务仍可能执行两次，所以扣库存、发交易这些操作自己要幂等。需要共识级别的互斥时，用 etcd 或 ZooKeeper 的租约。

### 3. 消息队列的选型依据是什么？RabbitMQ、Kafka、RocketMQ 有什么区别？

**参考答案**

先看顺序、吞吐、延迟消息、事务消息和能不能重放，再看团队能运维哪一个、Go 客户端是否成熟。投递按至少一次设计，消费侧用幂等键。

RabbitMQ 是 AMQP。交换机按 direct、topic、fanout、headers 绑定到队列，路由灵活。消费端是推送，用 prefetch 做流控。单条确认、死信、延迟（插件或 TTL 加死信）都顺手。吞吐通常低于 Kafka，适合任务分发和复杂路由。高可用用 quorum queue。顺序只在单队列、单消费者时比较好说。

Kafka 是分区日志，消费者组自己拉取。吞吐高，按时间或大小留存，可以按位点重放。顺序只在分区内成立；生产者开了幂等之后，分区内的重试也能保住顺序。它没有 RocketMQ 那种一等的任意延迟消息。适合流水、事件和要回溯的数据。

RocketMQ 用 NameServer 做路由，不依赖 ZooKeeper。队列类似分区，可以用 Tag 过滤。延迟消息、顺序消息、事务消息（半消息）是它常被选中的原因。4.x 的延迟是固定的十几个级别，5.x 可以指定延迟时间。官方有 Go 客户端。

流水和高吞吐用 Kafka。任务和复杂路由、堆积不大，用 RabbitMQ。业务事件要延迟或事务消息，并且团队运维得了，用 RocketMQ。要马上返回结果的查询仍然走 RPC，不把请求套进队列里做同步等待。

### 4. 区块链数据怎么存储？和传统数据库有什么区别？

**参考答案**

区块是块头加区块体。块头里有父区块哈希，所以改一个历史区块会让后面的哈希对不上。以太坊的状态根、交易根、回执根都是 Merkle Patricia Trie：状态按地址存账户的 nonce、余额、存储根和代码哈希，合约存储是另一棵 trie。Geth 现在默认把区块和 trie 节点放在 Pebble 这类键值库里，更早的版本常用 LevelDB。全节点会裁掉旧状态，归档节点才留全部历史状态。合并之后共识层和执行层分开存，业务上的账户和合约仍在执行层。

节点之间靠共识复制同一条链。按区块高度、交易哈希、地址读，是节点接口就能做的。按用户、时间范围、池子做统计，要自己索引事件日志，写进可查询的库。

传统数据库是原地更新，有二级索引和 SQL，由运营这台库的人保证内容。链上历史是追加的，单条记录可以被任何节点用哈希和默克尔证明核对。链的代价是确认慢、写入贵、复杂查询要另建索引。应用的做法是：结算以链为源，数据库保存投影，重组时按区块高度和哈希把投影滚回去。

### 5. 智能合约怎么交互？用过 web3.go 吗？

**参考答案**

读合约用 `eth_call`，只在节点里模拟，不上链也不花 gas。写合约要本地签一笔交易，用 `eth_sendRawTransaction` 广播，再等回执。没有一个唯一官方的 web3.go；这个名字通常指风格接近 web3.js 的社区客户端。Go 生产代码更多用 go-ethereum 的 `ethclient` 和 `abigen`。没用过那个名字的包，就说你用的是 `ethclient`，顺序是一样的。

函数选择子是函数签名 keccak256 的前 4 字节，参数按 ABI 编码。调用多了就用 `abigen` 生成类型安全的绑定。读路径里 `blockNumber` 传 nil 表示最新块：

```go
func balanceOf(ctx context.Context, client *ethclient.Client, token, holder common.Address) (*big.Int, error) {
	parsed, err := abi.JSON(strings.NewReader(`[{"name":"balanceOf","type":"function","stateMutability":"view","inputs":[{"name":"account","type":"address"}],"outputs":[{"name":"","type":"uint256"}]}]`))
	if err != nil {
		return nil, err
	}
	data, err := parsed.Pack("balanceOf", holder)
	if err != nil {
		return nil, err
	}
	out, err := client.CallContract(ctx, ethereum.CallMsg{To: &token, Data: data}, nil)
	if err != nil {
		return nil, err
	}
	vals, err := parsed.Unpack("balanceOf", out)
	if err != nil {
		return nil, err
	}
	bal, ok := vals[0].(*big.Int)
	if !ok || bal == nil {
		return nil, errors.New("balanceOf: unexpected type")
	}
	return bal, nil
}
```

写的步骤：`PendingNonceAt` 取 nonce，`SuggestGasTipCap` 取小费，`maxFeePerGas` 要盖住 baseFee 加小费并留余量，`EstimateGas` 得到 gas limit，组装 EIP-1559 的 `DynamicFeeTx`，用链 ID 签名后 `SendTransaction`。回执 `Status` 为 1 才是执行成功；0 表示已经进块但合约回滚，gas 仍然扣了。事件看收据里的日志，或按合约地址和 topic0 调 `eth_getLogs`。

私钥不放在对外 API 进程里。用户操作让钱包签；服务自己要发交易，就用独立的签名服务。同一个地址的 nonce 只能有一个分配者，多节点各查一次 pending nonce 会撞车。动账不要把 `latest` 上的一张收据当成最终结果，重组会把它拿掉，资金类操作等 `finalized`。

### 6. 如果让你设计一个 DEX（去中心化交易所）的后端，你会怎么考虑？

**参考答案**

合约负责兑换和资产结算，Go 后端负责索引、询价、拼交易和风控。私钥留在用户钱包。

链上若是 AMM，后端订阅 Swap、Sync、Mint、Burn 这些日志，把池子储备、价格和成交写进可查询的库，并记下区块高度和哈希。重组时按这个位点把受影响的池子重放回来。询价分两档：展示可以用索引状态，并带上区块高度；用户确认前再用 `eth_call` 调 Quoter，用 `amountOutMin` 和 `deadline` 卡住滑点。交易由后端拼 calldata，钱包签名后广播。节点至少准备两家，索引高度落后就告警，RPC 要限流。

订单簿要先定撮合放在哪。全部上链，吞吐受 gas 限制。链下撮合、链上结算，吞吐高，但要处理撤单、部分成交和结算失败，后端会变成可以审查的入口。AMM 的后端停了，用户仍可以直接调合约，所以后端不能把自己做成唯一入口。

索引会落后于链头，报价和成交可能不一致，确认前的那一次 `eth_call` 就是用来收这个口子的。大单还会碰到 MEV，可以接私有通道，但不能向用户承诺索引里的价格一定能成交。资产数量以回执和状态树为准，数据库只是投影。

### 7. Merkle Tree 是什么？有什么用？

**参考答案**

Merkle Tree 用哈希把一组数据收成一个根。叶子是数据的哈希，父节点是左右孩子哈希拼起来再哈希。任何一个叶子变了，根都会变。要证明某个叶子在这棵树里，只要带上从叶子到根的兄弟节点，大小是 O(log n)，不必交出整棵树。

比特币区块头里的交易根就是这样一棵二叉树。轻客户端只保存块头，加上一条证明，就能确认某笔交易被打进了这个块。奇数个叶子时，比特币会复制最后一个哈希；证明必须按这条链的规则算。

以太坊的状态根、交易根、回执根是 Merkle Patricia Trie。路径就是键，所以它证明的是「这个键对应这个值」，不只是「这个叶子出现过」。账户、合约存储和一笔交易有没有进块，都靠这种证明。空投合约则常只存一个根，用户提交自己的账户、金额和路径，合约验完再把这一位标记为已领取。Git 的提交对象也是同一类结构。面试里把二叉树和 Patricia trie 分开说，两种证明算法不能混用。

### 8. 以太坊 Gas 机制了解吗？怎么优化 Gas 费用？

**参考答案**

Gas 计量计算量，费用用 ETH 结算。一笔交易实际支付的是 `gasUsed * 有效 gas 价格`。EIP-1559 之后，协议按区块利用率调整 `baseFee` 并把它烧掉；用户设置 `maxFeePerGas` 和 `maxPriorityFeePerGas`。有效价格是 `min(maxFee, baseFee + tip)`，小费给提议者，没花掉的封顶部分退回。`maxFee` 低于当前 baseFee，交易就进不了块。每个区块有 gas 上限，目标用量大约是上限的一半，baseFee 按实际用量相对目标上下浮动。EIP-4844 之后，blob 有另一套 gas 市场，二层把数据放进 blob，比写进 calldata 便宜。

合约侧最贵的是存储。新开辟一个存储槽是万这一级的 gas，再加 EIP-2929 的冷访问；一次简单算术只有几 gas，精确价目随 EIP 变过，面试讲量级和冷热访问即可。能放进同一个 32 字节槽的状态变量排在一起。一个交易里反复读的槽先抄到内存。只给索引器用的数据写事件，不写存储。外部参数留在 calldata 里。循环不要扫一个无界的存储数组。能在链下算完的，链上只验签名或 Merkle 证明。`require` 的长字符串换成自定义错误。清存储的退款在 EIP-3529 之后有上限，不能靠退款把调用做成免费。

后端改不了已经部署的字节码。能做的是估准 `EstimateGas`、给 maxFee 留余量、同一 nonce 用更高的小费替换卡住的交易，以及不要在旧交易还可能进块时换一个新 nonce 把业务发两次。

### 9. 微服务架构下，服务之间怎么通信？HTTP、gRPC、消息队列选哪个？

**参考答案**

要立刻拿到结果就用 RPC。要削峰、扇出、允许晚一点处理，就用消息队列。对外和浏览器走 HTTP JSON，对内的 Go 服务优先 gRPC。

HTTP JSON 调试方便，网关、浏览器和第三方都认。schema 松，序列化开销大，流式要自己做 SSE 或分块。gRPC 跑在 HTTP/2 上，用 protobuf，有代码生成、deadline 和四种流，二进制和强类型适合内部调用。浏览器需要 grpc-web 或先转成 HTTP。负载均衡要认得 HTTP/2，否则一条连接上的多路复用会粘在一台后端。

消息队列的发送方不等处理完。消费按至少一次加幂等。它不适合「查这笔订单现在的状态」这种必须马上返回的请求。用队列模拟同步 RPC，延迟和故障都会更难排。

一条链路可以三段不同：入口 HTTP，内部查询 gRPC，成交后的通知进队列。超时、重试预算和幂等键比协议名字更决定能不能上线。重试只放在安全的读，或者带幂等键的写上。

### 10. 服务注册发现怎么做？Consul 和 Nacos 有什么区别？

**参考答案**

注册中心保存实例和健康状态，调用方订阅变更，再在客户端做负载均衡。跑在 Kubernetes 上时，Service 已经按 Endpoint 做了发现，流量不多就用它。要跨集群、虚拟机，或者配置也放在一起，再加 Consul 或 Nacos。

Consul 的服务端用 Raft 存目录，节点之间用 gossip 做成员和故障探测。机器上的 agent 做 HTTP、TCP、gRPC 或脚本健康检查，不健康的实例会从 DNS（默认 8600）和 HTTP API（默认 8500）的结果里消失。它自带 KV，灰度、权限和发布审计要自己补，所以经常只承担发现。多数据中心是它的强项。

Nacos 把发现和配置放在一个集群。2.x 的 SDK 用 gRPC 长连接注册和接收推送；连接断开后，临时实例会被摘掉，重连时再注册。临时实例走 AP，持久实例走 Raft，适合不能发心跳的外部资源。模型是命名空间、分组、服务名，官方有 Go SDK。

Consul 的健康检查和多数据中心更完整，配置只是 KV。Nacos 的发现和配置在一起，健康模型分成临时实例和持久实例。二十人的团队先用部署平台自带的服务发现，缺的那一块再加一个，避免两套注册中心各管一半。

### 11. 配置中心怎么做？Apollo 和 Nacos 有什么区别？

**参考答案**

配置中心解决的是：改一份配置，进程不用重新发布就能拿到，并且知道是谁在什么时候改的。启动参数放环境变量或本地文件。会频繁改、还要分环境的开关才进配置中心。

Apollo 按应用、环境、集群、namespace 组织。一次发布是一个不可变版本，可以灰度、回滚和审计。客户端用 HTTP 长轮询等通知，服务端大约挂起 60 秒，有变化再拉整份配置。经典部署包含 Config Service、Admin Service、Portal 和 Meta Server，还要数据库，组件多。Java 客户端最完整，Go 用社区客户端，能力可能落后一截。

Nacos 用 namespace、group、dataId。2.x 在发现用的那条 gRPC 长连接上推送配置变更，同一个集群就能做注册发现。历史和灰度都有，权限和发布流程通常没有 Apollo 那么细。官方 Go SDK 和这个岗位更贴。

人多、配置要审批和按机器灰度，用 Apollo。人少、希望发现和配置一个系统搞定，用 Nacos。密钥放在独立的密钥管理里，配置里只留引用。客户端启动时要能读到一份，并监听变更；回调里不做慢操作。配置中心短暂不可用时用上次的本地缓存，同时告警。

### 12. 接口限流怎么做？令牌桶和漏桶算法了解吗？

**参考答案**

入口用令牌桶限制平均速率，并允许一小段突发。下游只能匀速消化时用漏桶。超限直接拒绝，不把请求 goroutine 堵在等令牌上。

令牌桶按速率 r 补令牌，桶容量是 b。请求拿走令牌就通过，桶空就拒绝。空闲时攒下的令牌让短突发过去，长期平均仍是 r。`golang.org/x/time/rate` 就是这个模型。漏桶把请求放进队列，按固定速率流出，出口被抹平；队列满了就拒绝。突发会变成排队延迟。Nginx 的 `limit_req` 是漏桶思路。

固定窗口在整点会把相邻两个窗口的额度叠在一起。滑动窗口更平，实现比桶重。集群总额度放 Redis，用 Lua 一次完成按时间补令牌和扣减。单机限额只挡本进程的突发。维度按路由加用户或 API key，不单按 IP。返回 429，并告诉调用方何时重试。下单和链上写的额度比查询小。

### 13. 全链路追踪怎么做？SkyWalking 了解吗？

**参考答案**

一次请求有一个 trace，里面每个服务的一段工作是一个 span。进程内用 context 往下传。出进程时把 trace id、span id 写进 HTTP 头、gRPC metadata 或消息头。只在日志里打印、不往下游传，链路会在边界断掉。新起的 goroutine 和异步任务也要把 context 带进去。

SkyWalking 的服务端是 OAP，探针把数据报上去，UI 看拓扑、耗时和错误。Java 里常用字节码探针。Go 没有统一的字节码插桩，自动接入用官方的 skywalking-go，手写 span 用 go2sky。数据库、Redis、消息队列的客户端要包一层，只埋 HTTP 入口会得到一个看不出内部的大 span。它自己的跨进程头是 `sw8`。

高 QPS 上要采样，否则追踪本身成为负载。头部采样实现简单；想保住慢请求和错误，要用尾部采样，成本更高。新服务可以用 OpenTelemetry 打点，传播用 W3C `traceparent`，再导出到已经在跑的 SkyWalking OAP。导出前确认 OAP 打开了对应接收器。业务代码少绑一家探针的 API。

### 14. 解析一个 JSON-RPC 请求（10 分钟）

**参考答案**

按 JSON-RPC 2.0 把 body 解成单个请求或一批请求。`jsonrpc` 必须是 `"2.0"`，`method` 必须是非空字符串。`params` 可以省略，出现时只能是数组或对象。`id` 可以是字符串、数字或 null；只有根本没有 `id` 这个字段才是通知。带了 `id` 且值为 null，仍然要回包。合法通知不回包。`id` 缺失但报文本身不合法时，回一条 `id` 为 null 的错误，因为没有可以关联的 id。

body 无法按 JSON 解析时，回 `-32700`。空数组，以及顶层是数字、字符串、布尔值或 null 时，回一条 `-32600`，不包成数组。批量里每个元素各自校验。响应数组只包含需要回复的那些；全是通知时什么都不回。规范允许批量响应乱序，这里按请求顺序回。`method` 以 `rpc.` 开头是协议保留的，按找不到方法处理。业务错误用 `-32000` 到 `-32099`。`eth_call`、`eth_chainId` 都是这一个信封，参数放在 `params` 里。

```go
package jsonrpc

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"strings"
)

const Version = "2.0"

const (
	CodeParseError     = -32700
	CodeInvalidRequest = -32600
	CodeMethodNotFound = -32601
	CodeInvalidParams  = -32602
	CodeInternalError  = -32603
)

type Request struct {
	Method string
	Params json.RawMessage
	ID     json.RawMessage
	Notify bool
}

type Error struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

func (e *Error) Error() string { return e.Message }

type Response struct {
	JSONRPC string          `json:"jsonrpc"`
	Result  any             `json:"result,omitempty"`
	Error   *Error          `json:"error,omitempty"`
	ID      json.RawMessage `json:"id"`
}

type rawReq struct {
	JSONRPC string
	Method  string
	Params  json.RawMessage
	ID      json.RawMessage
	hasID   bool
	bad     bool
}

func (r *rawReq) UnmarshalJSON(b []byte) error {
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(b, &obj); err != nil {
		return err
	}
	if v, ok := obj["jsonrpc"]; ok {
		if err := json.Unmarshal(v, &r.JSONRPC); err != nil {
			r.bad = true
		}
	}
	if v, ok := obj["method"]; ok {
		if err := json.Unmarshal(v, &r.Method); err != nil {
			r.bad = true
		}
	}
	if v, ok := obj["params"]; ok {
		r.Params = append(json.RawMessage(nil), v...)
	}
	if v, ok := obj["id"]; ok {
		r.hasID = true
		r.ID = append(json.RawMessage(nil), v...)
	}
	return nil
}

func Parse(body []byte) (batch bool, reqs []Request, resps []Response, fatal *Response) {
	body = bytes.TrimSpace(body)
	if len(body) == 0 || !json.Valid(body) {
		return false, nil, nil, errResp(nil, CodeParseError, "Parse error")
	}
	switch body[0] {
	case '[':
		var elems []json.RawMessage
		if err := json.Unmarshal(body, &elems); err != nil {
			return true, nil, nil, errResp(nil, CodeParseError, "Parse error")
		}
		if len(elems) == 0 {
			return true, nil, nil, errResp(nil, CodeInvalidRequest, "Invalid Request")
		}
		for _, el := range elems {
			req, resp := parseOne(el)
			if req != nil {
				reqs = append(reqs, *req)
			}
			if resp != nil {
				resps = append(resps, *resp)
			}
		}
		return true, reqs, resps, nil
	case '{':
		req, resp := parseOne(body)
		if req != nil {
			reqs = append(reqs, *req)
		}
		if resp != nil {
			resps = append(resps, *resp)
		}
		return false, reqs, resps, nil
	default:
		return false, nil, nil, errResp(nil, CodeInvalidRequest, "Invalid Request")
	}
}

func parseOne(body []byte) (*Request, *Response) {
	body = bytes.TrimSpace(body)
	if len(body) == 0 || body[0] != '{' {
		return nil, errResp(nil, CodeInvalidRequest, "Invalid Request")
	}
	var raw rawReq
	if err := json.Unmarshal(body, &raw); err != nil {
		return nil, errResp(nil, CodeInvalidRequest, "Invalid Request")
	}
	if !raw.hasID {
		if raw.bad || raw.JSONRPC != Version || raw.Method == "" {
			return nil, errResp(nil, CodeInvalidRequest, "Invalid Request")
		}
		if !validParams(raw.Params) {
			return nil, errResp(nil, CodeInvalidParams, "Invalid params")
		}
		return &Request{Method: raw.Method, Params: raw.Params, Notify: true}, nil
	}
	id := raw.ID
	if !validID(id) {
		id = nil
	}
	if raw.bad || raw.JSONRPC != Version || raw.Method == "" || !validID(raw.ID) {
		return nil, errResp(id, CodeInvalidRequest, "Invalid Request")
	}
	if !validParams(raw.Params) {
		return nil, errResp(raw.ID, CodeInvalidParams, "Invalid params")
	}
	return &Request{
		Method: raw.Method,
		Params: raw.Params,
		ID:     append(json.RawMessage(nil), raw.ID...),
	}, nil
}

func validParams(p json.RawMessage) bool {
	p = bytes.TrimSpace(p)
	if len(p) == 0 {
		return true
	}
	return p[0] == '[' || p[0] == '{'
}

func validID(raw json.RawMessage) bool {
	raw = bytes.TrimSpace(raw)
	if len(raw) == 0 {
		return false
	}
	if bytes.Equal(raw, []byte("null")) {
		return true
	}
	if raw[0] == '"' {
		var s string
		return json.Unmarshal(raw, &s) == nil
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var n json.Number
	if err := dec.Decode(&n); err != nil {
		return false
	}
	var extra any
	if err := dec.Decode(&extra); err != io.EOF {
		return false
	}
	return true
}

func errResp(id json.RawMessage, code int, msg string) *Response {
	if len(bytes.TrimSpace(id)) == 0 {
		id = json.RawMessage("null")
	}
	id = append(json.RawMessage(nil), bytes.TrimSpace(id)...)
	return &Response{
		JSONRPC: Version,
		Error:   &Error{Code: code, Message: msg},
		ID:      id,
	}
}

type Handler func(ctx context.Context, params json.RawMessage) (any, error)

func Call(ctx context.Context, req Request, methods map[string]Handler) *Response {
	if strings.HasPrefix(req.Method, "rpc.") {
		if req.Notify {
			return nil
		}
		return errResp(req.ID, CodeMethodNotFound, "Method not found")
	}
	h, ok := methods[req.Method]
	if !ok {
		if req.Notify {
			return nil
		}
		return errResp(req.ID, CodeMethodNotFound, "Method not found")
	}
	result, err := h(ctx, req.Params)
	if req.Notify {
		return nil
	}
	if err != nil {
		var rpc *Error
		if errors.As(err, &rpc) {
			return errResp(req.ID, rpc.Code, rpc.Message)
		}
		return errResp(req.ID, CodeInternalError, err.Error())
	}
	if result == nil {
		result = json.RawMessage("null")
	}
	return &Response{JSONRPC: Version, Result: result, ID: req.ID}
}
```

### 15. 用 Go 实现一个 LRU Cache（15 分钟）

**参考答案**

用 map 做 O(1) 查找，双向链表维护使用顺序。最近使用的放在头哨兵后面，淘汰链表尾部。`Get` 会改顺序，所以和 `Put` 共用一把互斥锁，不能用读锁把 `Get` 包成纯读。每次操作 O(1)，空间 O(容量)。

map 和链表在同一把锁里改。锁只包住指针操作。结构体含 `sync.Mutex`，只能按指针传递，按值复制会把锁拷走。

容量为 2 时，依次 `Put(1,1)`、`Put(2,2)`、`Get(1)`、`Put(3,3)`，应该淘汰 2。更新已存在的 key 只改值并移到表头，不增加长度。

```go
package lru

import (
	"errors"
	"sync"
)

type entry[K comparable, V any] struct {
	key        K
	val        V
	prev, next *entry[K, V]
}

type Cache[K comparable, V any] struct {
	cap        int
	mu         sync.Mutex
	items      map[K]*entry[K, V]
	head, tail *entry[K, V]
}

func New[K comparable, V any](capacity int) (*Cache[K, V], error) {
	if capacity < 1 {
		return nil, errors.New("capacity < 1")
	}
	head, tail := &entry[K, V]{}, &entry[K, V]{}
	head.next = tail
	tail.prev = head
	return &Cache[K, V]{
		cap:   capacity,
		items: make(map[K]*entry[K, V], capacity),
		head:  head,
		tail:  tail,
	}, nil
}

func (c *Cache[K, V]) Get(key K) (V, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	e, ok := c.items[key]
	if !ok {
		var zero V
		return zero, false
	}
	c.moveFront(e)
	return e.val, true
}

func (c *Cache[K, V]) Put(key K, val V) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if e, ok := c.items[key]; ok {
		e.val = val
		c.moveFront(e)
		return
	}
	if len(c.items) == c.cap {
		old := c.tail.prev
		c.detach(old)
		delete(c.items, old.key)
	}
	e := &entry[K, V]{key: key, val: val}
	c.items[key] = e
	c.pushFront(e)
}

func (c *Cache[K, V]) moveFront(e *entry[K, V]) {
	c.detach(e)
	c.pushFront(e)
}

func (c *Cache[K, V]) detach(e *entry[K, V]) {
	e.prev.next = e.next
	e.next.prev = e.prev
}

func (c *Cache[K, V]) pushFront(e *entry[K, V]) {
	e.prev = c.head
	e.next = c.head.next
	c.head.next.prev = e
	c.head.next = e
}
```

## 三面

**现场记录**

CEO 面，大约 30 分钟，聊得比较随意，主要看价值观合不合。

### 1. 你对 Web3 这个行业怎么看？未来会怎么发展？

**答题思路**

把判断分成你能负责的工程，和你控制不了的行情。

1. 先承认周期：交易量和融资会随行情起落，公司跑道和监管也会变。薪资高和这些风险是一起的。
2. 再说你认为会留下来的工作：资产结算、索引、钱包接入、风控。这些在交易冷清时仍然要正确。
3. 你的选择落在资金不能错、服务能够恢复。若公司的主要收入建立在你不愿意碰的模式上，这轮就把边界问清楚。
4. 未来只讲你能论证的结构：更多用户操作会落在二层网络，一层负责结算，查询靠索引。不预测价格。

### 2. 你价值观是什么样的？认同什么？

**答题思路**

用行为定义，不用形容词堆。每条准备一个真实例子，不编公司名。

1. 先说一条你用行动守过的底线：资金或数据出错时先停写、先对账，再谈功能进度。
2. 再说合作方式：不同意会直接说，说完按约定执行；答应的故障处理会跟到恢复。
3. 对齐小团队：能接受没有厚厚的职级，也希望拍板的人说得清。
4. 被问「认同什么」时，把「对用户资产保守」放在「对新技术好奇」前面。

### 3. 你技术上的追求是什么？想做点有意义的事，还是只为赚钱？

**答题思路**

两件事同时说，各用一句。

1. 赚钱：你在看机会，薪酬是原因之一。可以说在意收入。
2. 有意义：对你来说，意义是系统在资金和数据上经得起核对，出了故障能定位。
3. 落到这个公司：20 人团队里，一个人会碰到索引、节点和接口整段。你想要的是这段责任，同时接受这里可能没有大厂的稳定。
4. 若对方逼你排顺序：先保证资产正确，再谈增长功能。

### 4. 你能接受公司文化吗？我们是扁平化管理。

**答题思路**

先说接受，再把「扁平」翻译成具体工作方式，确认和你的习惯一致。

1. 接受的部分：需求可以直接找负责的人，代码评审和故障不用层层转述。人少时你愿意值班，写索引也会改接口。
2. 需要确认的部分：生产事故谁有权停机，合约升级谁签字，意见不一致时谁在当天拍板。每件事仍然要有一个 owner。
3. 用一次真实的合作说明：你怎么提出反对意见，最后怎么执行决定。
4. 值班频率和职责边界按你能接受的范围说，说得太满会被追问到具体排班。

### 5. 你什么时候能入职？有什么想问我的？

**答题思路**

入职给一个真实日期。现有工作的交接需要多久，有没有已经答应的发布，说一个区间，例如交接完成后两周。能到岗的日期以你的交接事实为准。

反问留三个，都和你还在犹豫的点有关：

1. 跑道和合规：公司目前靠什么收入，哪些业务是明确不做的。
2. 技术决策：CTO 和 CEO 意见不同时谁拍板；私钥、合约升级、节点这些权限怎么分开。
3. 近一个季度要上线什么，后端现在几个人，谁值生产。

问完把对方的回答和你的底线对一下，例如值班频率、是否要碰托管资金。这轮是互相确认。

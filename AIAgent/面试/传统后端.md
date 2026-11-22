# 传统后端学习笔记

范围是博客列表第 32–35 页里和这些主题直接相关的正文：Elasticsearch、秒杀、中台、Redis、MySQL、消息队列（RabbitMQ、RocketMQ、Kafka）、Nacos、Gateway、Java 并发，以及 Docker、Docker Compose、Kubernetes。笔记按作者原文里的机制整理，不是目录摘要。原文在 CSDN「计算机王」（[qq_36301061](https://blog.csdn.net/qq_36301061)）。同一篇「并发设计模式」在列表里重复出现了四次，正文相同，这里只记一次。

篇目链接在文末。

---

## 1. Docker

Docker 被当成交付应用的容器引擎：把应用和它依赖的基础设施（MySQL、消息队列、JDK、Tomcat）隔离开，镜像从仓库拉下来就能跑。类比是 Maven：Maven 仓库放 jar，Docker 仓库放跑起来所需的整套环境。

安装按 CentOS 7 来写。内核要高于 3.10（`uname -r`），用 root 把 yum 更新到最新，卸掉旧的 `docker*`，装 `yum-utils`，把阿里云的 `docker-ce.repo` 加进去，`yum makecache fast` 之后可以 `yum list docker-ce --showduplicates` 看版本。文中指定安装的是 `docker-ce-3:19.03.9-3.el7.x86_64`。启动并开机自启：`systemctl start docker && systemctl enable docker`。`docker version` 同时出现 Client 和 Server 才算装好。镜像加速去阿里云容器镜像服务里抄 daemon 配置，然后 `systemctl daemon-reload` 再 `restart docker`。卸载是 `yum remove` 加上删掉 `docker.service.d`、`/var/lib/docker`、`/var/run/docker`。

镜像命令：`docker search` 在 Docker Hub 上搜，结果里看仓库名、描述、收藏数、是否官方、是否自动构建。`docker pull java:8` 这种带标签才是指定版本，不带标签拉的是 latest。`docker images` 看本地仓库、标签、镜像 ID、创建时间和大小。`docker rmi` 删本地镜像，`-f` 强制，`docker rmi $(docker images -q)` 全删。

容器最常用的是 `docker run`。`-d` 后台跑，`-P` 随机映射端口，`-p` 指定映射，四种写法是 `ip:宿主机端口:容器端口`、`ip::容器端口`、`宿主机端口:容器端口`、只写容器端口。网络模式 `--net`：`bridge` 是默认网桥，`host` 直接用宿主机网络，`container:名字或ID` 复用另一个容器的网络，`none` 不配网络。例子是 `docker run -d -p 91:80 nginx`，本机没有这个镜像时会先去拉。`docker run` 创建时如果本地没有镜像，Docker 会先拉取。

看资源用 `docker stats`，大约一秒刷一次；`--no-stream` 只打当时那一帧。

### Dockerfile

Dockerfile 是描述怎么做出镜像的文本。改 Nginx 首页的最小例子：`FROM nginx`，再用 `RUN echo ... > /usr/share/nginx/html/index.html`。在文件所在目录执行 `docker build -t nginx:tuling .`，最后那个点是上下文路径。然后 `docker run -d -p 92:80 nginx:tuling`。

常用指令：`FROM` 指定基础镜像（名字加版本，比如 `java:8`）；`RUN` 在构建阶段执行命令；`ADD` 把上下文里的文件、URL 或压缩包放进镜像；`COPY` 只拷文件，不支持 URL 和压缩包；`CMD` 和 `ENTRYPOINT` 都是容器启动后要执行的命令，可以写多次但只有最后一条生效；`EXPOSE` 声明对外端口；`WORKDIR` 指定工作目录；`ENV` 设环境变量；`USER` 指定后面 `RUN`、`CMD`、`ENTRYPOINT` 用哪个用户；`VOLUME ["/data"]` 把容器里的目录变成可持久化、可共享的挂载点。

微服务镜像用 Eureka 的 jar 演示。Dockerfile 基于 `java:8`，`ADD` 把 jar 放成 `/app.jar`，`EXPOSE 8761`，`ENTRYPOINT java ${JAVA_OPTS} -jar /app.jar`。构建命令 `docker build -t microservice-eureka-server:0.0.1 .`，启动 `docker run -d -p 8761:8761 ...`。`-v /log:/container-log` 把宿主机目录挂进容器。JVM 参数用 `-e JAVA_OPTS='-Xms...'` 传进去。要在容器里跑 `jinfo`、`jmap`，加上 `--cap-add=SYS_PTRACE`。

推到 Docker Hub：先 `docker login`，再用 `docker tag` 在镜像名前加上账号名，然后 `docker push`。

### Compose

一个系统里往往有很多微服务、每个还有多个实例，一个一个 `docker run` 维护不了。Compose 用一份 YAML 描述所有服务，一条命令创建并启动。文中用 shell 安装 1.28.6：把对应系统架构的二进制下到 `/usr/local/bin/docker-compose`，`chmod +x`，`docker-compose --version` 验证。

Eureka 的目录是上层 `docker-compose.yml`，下层 `eureka/Dockerfile` 和 jar。文件里 `version: '3.8'`，`services.eureka` 指定 `image`、`build: ./eureka`、`ports: "8761:8761"`、`expose: 8761`。在 YAML 所在目录 `docker-compose up`，加 `-d` 后台跑。

字段含义：`image` 镜像不在本地就去 pull；`build` 可以是路径，也可以是带 `context`、`dockerfile`、`args` 的对象；`command` 覆盖容器启动命令，字符串或列表都行；`links` 用 `服务名` 或 `服务名:别名` 连到同文件里的其他服务；`external_links` 连到这份 Compose 管不到的容器；`ports` 是宿主机到容器的映射，只写容器端口时宿主机随机分配；`expose` 只给连过来的服务看，不映射到宿主机；`volumes` 是 `宿主机:容器`，可以再加 `:ro`。

---

## 2. Kubernetes

单机 Docker 部署分布式系统很麻烦。Kubernetes（文中读作库波奈第丝）是在 Docker 之上管跨机器容器集群的工具。Docker 自己的 Swarm 也做这件事，笔记里的判断是 Swarm 被 K8s 比下去了。

核心能力按官方特性来记：不用改应用就能做服务发现和负载均衡；存储编排，包括本地存储；Secret 和配置可以更新，不必为了改配置重打镜像，也不把密钥写进镜像；除了常驻服务还能跑批处理和 CI；按命令、界面或 CPU 做水平扩缩；分步上线并盯健康状况，避免一次停光所有实例；按资源约束装箱；节点挂了或健康检查失败就重启、换节点、杀掉容器。

安装用三台至少 2 核 4G、内存不低于 2G 的 CentOS 7 虚拟机，先装好和 K8s 版本匹配的 Docker。三台都做：关防火墙并禁止开机启动；`setenforce 0` 临时关 SELinux，改 `/etc/selinux/config` 永久关；`swapoff -a` 并注释 `/etc/fstab` 里的 swap，重启后 `free -m` 里 swap 应为 0；主机名设成 `k8s-master`、`k8s-node1`、`k8s-node2`；master 的 `/etc/hosts` 写三台 IP；打开桥接流量转发到 iptables（`bridge-nf-call-ip6tables` 和 `bridge-nf-call-iptables`），`sysctl --system`；`ntpdate time.windows.com` 对时；yum 源用阿里云 kubernetes-el7。装的是 `kubelet`、`kubeadm`、`kubectl` 的 1.18.0，`systemctl enable --now kubelet`。master 上用 kubeadm 初始化，镜像仓库指到 `registry.aliyuncs.com/google_containers`，版本 `v1.18.0`，`--service-cidr=10.96.0.0/12`，`--pod-network-cidr=10.244.0.0/16`，`--apiserver-advertise-address` 换成 master 自己的 IP。

### 对象

Deployment 负责创建和更新应用实例，把实例调度到各节点。节点没了，控制器在别的节点补上。白话就是：以前每台机器都要自己 `docker run`，配好从节点之后，在主节点提交 Deployment，下载和运行由它做。

Pod 是跑应用实例的逻辑主机，里面可以有多个容器。和单个 Docker 容器的差别是，同一个 Pod 里的容器共享存储、网络和运行信息。可以把它理解成容器跑在 Pod 上。

Service 把一组 Pod 用标签收成一个访问入口，负责对外暴露、负载均衡和服务发现。暴露方式：`ClusterIP` 是默认，只有集群内能访问；`NodePort` 在每个选定节点的同一端口做 NAT，用 `节点IP:NodePort` 从外面进，它是 ClusterIP 的超集；`LoadBalancer` 在支持的云上建外部负载均衡并给一个固定外部 IP，它是 NodePort 的超集；`ExternalName` 返回 CNAME，不走代理。

K8s 里一切都是资源，实例化之后叫对象。工作负载有 Pod、ReplicaSet、Deployment、StatefulSet、DaemonSet。服务发现和负载均衡有 Service、Ingress。配置和存储有 Volume、CSI，以及 ConfigMap（当配置中心）、Secret（敏感数据）、DownwardAPI（把外部环境信息交给容器）。这些在命名空间级别。集群级有 Namespace、Node、Role、ClusterRole、RoleBinding、ClusterRoleBinding。元数据类有 HPA（Pod 水平扩展）、PodTemplate、LimitRange。

资源一般用 YAML 清单创建，而不是临时敲命令。五个一级字段：`apiVersion`（没写 group 时默认 core，可用 `kubectl api-versions` 看当前集群有哪些）、`kind`、`metadata`（`name`、`namespace`、`labels`、给人看的 `annotations`）、`spec`（期望状态）、`status`（当前状态，只能由 K8s 自己维护）。`kubectl create deployment ... --dry-run -o yaml` 可以先打出一份清单再改。例子把 Deployment 改名为 `my-tomcat-yaml`，`replicas: 2`，`selector.matchLabels` 和 Pod 模板上的 `app` 标签保持一致。

---

## 3. 中台

中台是 2015 年阿里提出「小前台 + 大中台」之后，国内做微服务时绕不开的说法。作者的定义是：把各业务线里能复用的能力抽出来，去掉个性、留下共性，变成可复用组件，以后开发成本更低、质量更稳。分三类。业务中台是各条线都能用的业务组件，例如用户权限、会员、移动支付。数据中台把全业务的数据统一存储、建模、计算，给分析用。技术中台把各系统都要用的技术框架封起来，降低上层业务的门槛。

建设不只是技术问题，要动公司的资源。很多团队按产品、设计、开发、测试、运维横向切，天猫一拨人、淘宝一拨人，权限、订单、支付、商品各自做一份，共性抽不出来，这叫烟囱式团队。中台是战略，不是所有场景都该上，阿里内部也有拆中台的声音，成败要自己判断。

落地技术中台被写成三个阶段：单体拆成微服务；再把各业务线的共有能力收成一个个平台；最后调整组织架构和资源投入，才叫中台战略。作者补了一句落地条件：项目得足够大。和微服务的差别他是这样划的：微服务把请求转到某一个服务，中台把请求转到一整套被抽出来的共用系统，这套系统本身由可复用业务组成。

---

## 4. 秒杀

普通电商链路是查商品、创建订单、扣库存、更新订单、付款、发货。秒杀是进活动页、进详情、点立即秒杀、去付款。业务特点：价格低、推广猛、瞬间卖光、定时上架、瞬时并发高、限时限量限价、持续时间短，还要预热（今天的页面上提前放明天的商品）。

营销被分成三层。商品级：限时促销、限时抢购、秒杀、商品包邮。订单级：满赠、满减、送券、折扣、会员折扣、订单包邮。全站：优惠券、银行促销、支付红包、团购预售、砍价。商品限时秒杀是限时促销的加强版，多了排除已参加活动的商品、限制购买次数和种类、未付款取消时间、活动库存，渠道可以是 app、pc、wap 等。订单满减支持满 X 元或满 X 件、叠加、限制参与次数、按分类品牌商品和会员等级过滤。银行促销按周固定几天、总次数和每日次数，在支付时减免。

技术上把秒杀逻辑单独部署（单一职责），再配合错峰、限流熔断降级、队列削峰、预热后快速扣减、动静分离。问题本质是并发读（查库存）和并发写（建订单），写会超卖。

第一层过滤：10 个库存、1000 个请求时，库存放一份在 Redis，用 `DECR` 原子扣减。减到 0 之后在本进程打一个售罄标记，后面的请求直接返回。只有扣减成功的请求才进数据库，流量呈倒三角。多实例时这个本地标记是懒加载的，别的实例看不到，要用 Redis 的 channel 做同步。

第二层：建订单要插多张表，数据库成了瓶颈，把创建订单丢进消息队列削峰。

第三层：有人绕过代码、用客户端直接改库，缓存和库会对不上。用 Canal 跟 binlog。Canal 是阿里的 Java 项目，伪装成 MySQL slave，向 master 发 dump，master 推 binary log，Canal 解析 byte 流。MySQL 要开 `log-bin`、`binlog-format=ROW`、`server-id`（不能和 Canal 的 slaveId 重复），并 `binlog-do-db` 指定库。给 canal 用户 `SELECT, REPLICATION SLAVE, REPLICATION CLIENT`。实例配置在 `conf/example/instance.properties`，`./startup.sh` 启动。Java 客户端用 `canal.client` 1.1.0，`CanalConnectors.newSingleConnector` 连 `11111` 端口的 example 实例，`subscribe(".*\\..*")` 后按批拉 `Entry`，从 `RowChange` 里拿变更行。作者把它用在「主从权限、binlog、消息中间件」这条同步链上：库被改了，解析日志再通知下游，而不是只信应用自己写的缓存。

---

## 5. Elasticsearch

ES 是 Java 写的搜索引擎，作者用来替代高并发下 MySQL 的 `LIKE`。底层是 Lucene。Lucene 只能单机、只能嵌在 Java 里、代码多；ES 把这些补上，可以像 MySQL 一样单独部署。概念对应关系：index 是索引库，document 是一行，field 是列，mapping 是字段类型定义。7.x 起 type 统一写成 `_doc`。cluster 是多节点持有同一份数据，node 是集群里的一台服务器。

倒排索引用三行文本讲清楚：先按空格切开，单词去重后记下它出现在哪些文档 id 上，再按单词排序。查的是「内容到 id」，不是「id 到内容」。作者认为常用词有限，索引词表不会像 MySQL 行数那样无限涨；查出 id 后再回原文档。MySQL 仍要留着，因为 ES 语法乱、关联查询别扭，适合当搜索引擎，不适合当业务主库。

中文分词用 IK。`ik_max_word` 最细，`ik_smart` 最粗。用 `POST _analyze` 看切分结果。索引的增删查：`PUT /索引名` 创建，`GET` 查看，`DELETE` 删除。文档用 `PUT /索引/_doc/id` 指定 id 写入或覆盖，`POST` 不写 id 时由 ES 生成。`GET /索引/_search` 查该索引下的文档。批量取不同索引的文档走 `_mget`，body 里每个 `docs` 元素带 `_index`、`_id`（type 在 7.x 只能写 `_doc`）。

### 商品搜索怎么建模

京东搜索页被拆成四块：关键字、品牌（带 logo 的分组）、属性分组、筛选条件。品牌和属性随商品变化，不能写死。索引 `product_db` 里，`name`、`keywords`、`subTitle` 用 `text` 加 `ik_max_word`；价格、图片、品牌名、类目名用 `keyword`（精确匹配、不做分词）；库存布尔、销量、上架日期、品牌 id、类目 id 用对应的数值或日期类型。属性是一对多，用 `nested`，里面是 `attrId`、`attrName`、`attrValue`，避免对象数组被拍扁后张冠李戴。

Java 侧把前端参数收成 `ESRequestParam`：关键字、多选品牌 id、类目、排序（价格、销量、热度，升降序）、是否有货、价格区间、属性筛选、页码。Controller 把参数交给搜索服务，服务把上一篇的 DSL 翻成 Java 客户端请求。地址这种结构不要拆成多个顶级字段硬查，进阶篇把它做成对象：`address.province`、`address.city`、`address.street`，各自 `keyword`。

### 集群

节点分 Master 和 Data。启动时用 Zen Discovery 找到其他节点并建连。Master 管建删索引、分配分片、维护元数据和集群状态，不负责写入和查询，一个集群只有一个 Master。Data 节点承担数据的写入和查询。

分词器可以自己拼。`char_filter` 先做字符替换，例如 mapping 把 `&` 换成 `and`，`html_strip` 去掉标签。`tokenizer` 用 `standard` 切开。`filter` 再做 `lowercase` 和自定义 stop（丢掉 `the`、`a`）。整条链挂在 `type: custom` 的 analyzer 上，用 `_analyze` 看切分结果。英文停用词也可以直接在 standard 上设 `stopwords: _english_`。

高亮是搜索结果里把命中的词标出来。字段很长时不能整段返回：`fragment_size` 是每个片段的长度，默认 100；`number_of_fragments` 是最多返回几个片段。

聚合里 bucket 是分组，对应 SQL 的 `GROUP BY` 之后的每一组；metric 是对组做的统计，对应 `COUNT`、平均、最大、最小。商品搜索页上的品牌列表、属性列表，就是对 `brandName`、嵌套属性做 terms 聚合得到的 bucket。

---

## 6. Java 并发

### 内存模型

JMM 是 Java 线程通过共享内存通信的模型。可见性靠 `volatile` 触发内存屏障。笔记里把 JVM 的 StoreLoad 对应到汇编 `lock; addl $0,0(%%rsp)`：`lock` 前缀本身不是屏障指令，但有屏障效果，让其他核的缓存失效。

原子性这里说的不是 `i++`。在 32 位处理器上，`long` 和 `double` 占 64 位，要读两次，两次之间若插入写，读到的就不是同一次写入。`volatile` 用总线锁定保证这种 64 位读是一次完成的。有序性同样围绕 `volatile`：禁止特定的重排序。

### 线程状态与终止

操作系统视角五态：初始（Java `new` 了，操作系统还没真正创建）、可运行（已创建，还没分到 CPU）、运行、休眠（阻塞 API 或等别的线程）、终止（异常或 `run` 结束）。

两阶段终止：T1 发终止指令，T2 响应。线程要先回到 RUNNABLE 才能走向终止，所以用 `interrupt()` 把休眠中的线程叫醒，再让它自己把 `run` 跑完。只查标志位不够，因为线程可能正睡；只查中断状态也不够，第三方库捕获 `sleep` 的中断异常后常常不把中断位设回去。稳妥做法是自己维护终止标志，并在捕获中断后恢复中断状态。

避免共享有三种：不可变（Immutability，属性不能改，只读就没有并发问题）、写时复制（Copy-on-Write，注意拷贝成本）、线程封闭（Thread-Specific Storage，注意异步时线程变了，ThreadLocal 对不上）。

### synchronized、锁的思想、偏向锁

`counter++` 和 `counter--` 并发会丢更新。`synchronized` 把临界区串行化。写在方法上是 `ACC_SYNCHRONIZED` 标记；写在代码块上是进入时 `monitorenter`、离开时 `monitorexit`。管程是 MESA 模型：一个入口等待队列做互斥，多个条件队列做等待和唤醒，Java 里落在 `Object` 的 `wait` / `notify`。

抢锁按 ReentrantLock / AQS 的思路：先看同步队列里有没有因为没抢到而排队的线程。头节点的 next 不为空，说明队列里已经有人，后来的线程不该插队。AQS 被拆成六件事：抢锁、放锁、入队、出队、阻塞、唤醒。

偏向锁的 C++ 在 `BiasedLocking`。`init` 的注释讲的是延迟偏向：应用启动时会打大量安全点，这时如果还要撤销偏向，启动会变慢，所以偏向锁延后开启。理想情况是降低「取消某一个对象偏向」的成本，而不是靠这个延迟机制。

### CAS 与原子类

CAS 是比较并交换：线程读到内存值时抄一份预期值，写回去之前再比一次。没被别人改过就写入并返回新值，被改过就返回旧值，调用方重试。Java 的原子类包的就是这个循环。

### ReentrantLock 与读写锁

`synchronized` 是 JVM 实现的，线程中断、加锁解锁时机程序控不住。`ReentrantLock` 在 Java 层用 AQS 做同样的互斥，能响应中断、能尝试加锁、能多条件等待。`java.util.concurrent` 里多数同步器都绕着 AQS：等待队列、条件队列、独占获取、共享获取。

读写锁把读和写拆开。`readLock` 包查询，`writeLock` 包修改，都在 `finally` 里解锁。多个读可以同时持有，写互斥。

### Semaphore、CountDownLatch、CyclicBarrier

信号量是操作系统 PV 原语。P 是把 S 减 1，减完仍大于等于 0 就继续，小于 0 就进等待队列；V 是释放。许可数为 1 时等价互斥锁，许可数为 n 时只允许 n 个线程同时通过，用来限流。CountDownLatch 用来等一组任务都做完再往下走。CyclicBarrier 相反：一组线程各自 `await`，到齐之前全部堵住，到齐后一起放行，并且可以循环再用。两个构造参数，一个是参与线程数，一个是可选的 `barrierAction`，在全部到达之后、放行之前先跑这段逻辑。

### 阻塞队列

`Queue` 的约定：`add` 满了抛异常，`offer` 满了返回 false；`remove` 空了抛异常，`poll` 空了返回 null。`ArrayBlockingQueue` 有界数组，`LinkedBlockingQueue` 链表。`SynchronousQueue` 容量就是 1，不能扩、不能设容量，CAS 加自旋，自旋到阈值再阻塞。公平模式用 FIFO 链表，非公平用栈式链表（指针往前指）。一次只交接一个元素。`LinkedTransferQueue` 把同步队列和链表阻塞队列合在一起，API 可以做出同步交接的效果，同样是自旋到阈值再阻塞。

### Fork/Join、Future、线程池

CPU 密集任务（加密、压缩、纯计算）的线程数设成核数的 1 到 2 倍。再多也不会更快，因为每个核已经满负载，多出来的线程只增加上下文切换：保存当前线程状态、挂起、换上下一个线程的数据。Fork/Join 用来把这种大计算拆开再汇总。

`Runnable` 没有返回值。`Callable` 有。`Future` 拿结果，`FutureTask` 把 Callable 包成可提交、可查询的任务。作者把 Future 理解成「先拿到一个未来的结果句柄，计算完成再取」，用来做无阻塞式的结果传递。Disruptor 那篇是把这种无锁队列拿来做高吞吐的线程间传递，正文很长，核心是用环形缓冲替代加锁队列。

手写线程池之前要先有条件变量，而且条件变量必须和互斥锁一起用。笔记对照的是 Linux：`pthread_mutex_t`，属性里 `NORMAL` 抢不到就阻塞，`RECURSIVE` 同一线程可重入。作者当时没把「为什么条件变量离不开互斥锁」写成白话，只先记下这个搭配。

---

## 7. 高并发读多写少：静态化、缓存、本地缓存

商品模块按页面拆名词：分类（商品属于一个分类）、属性（一个商品 N 个属性，不同商品属性不同）、品牌（商品属于一个品牌）、参数、活动、评价。表如果合成一张，查询简单、存储极难；拆成多张，业务清楚、查询要 join 很多次。筛选是一版一版加上去的：只有分类时点了鞋就看不到裤子；加上品牌能看一个品牌下的鞋和裤子，但分不出七分裤；加上属性可以选裤型；再加上规格（尺码、64G/128G）才够详情页用。

前台展示再拆维度：商品（标题、图片、属性）、主商品（介绍、规格参数）、分类、商家、店铺。价格、促销、广告词、配送、预售这类实时数据异步加载，不放进静态页。一个详情页多个异步请求，再加上爬虫和比价，每秒上万。做法是后台发布商品时用模板提前生成 HTML，放到 CDN（作者写成 cds），用户打到离他近的缓存，不打源站。

模板引擎是 FreeMarker。`Configuration` 指定版本和模板目录、编码，`getTemplate` 加载 `.ftl`，数据放 Map 或 POJO，`Template.process` 写到文件。语法：`<#list>` 循环，`<#if>` 分支，`a??` 判断空，`?date` 格式化日期。模板只关心怎么展示，数据在模板外准备。

全量静态化的问题是模板一改，所有商品都要重新生成。于是改成缓存。缓存带来新问题：和数据库不一致、多一次网络 IO。不一致用短过期换最终一致，例如 5 秒过期，5 秒后对齐，5 秒内允许旧。强一致的数据不要放缓存。网络 IO 用 JVM 本地缓存挡一层：本地 Hash 没有再去 Redis。

三种失效要分开。击穿是一个热点 key 刚好过期，大量请求同时打到数据库，用分布式锁，只放一个请求去加载。穿透是查根本不存在的 key，空对象能挡重复的同一个 key，挡不住每次都换 key 的攻击，改用布隆过滤器。雪崩是大批 key 同时过期，过期时间加随机，再配合分布式锁。

布隆过滤器是一大段 bit。写入时对 key 做多次 hash，把对应 bit 置 1。查询时同样 hash，有一次落在 0 上就一定不存在；全是 1 只是可能存在，这时才放行去 MySQL，碰撞后的量数据库扛得住。只能加不能删，要删就重建，所以隔一段时间用库里的主键重新灌。Guava 没有现成实现时用 Redisson。空间按 bit 算，上亿也只是十几 MB，只存 key。

本地缓存不能只用 `ConcurrentHashMap`，因为没有淘汰策略。Guava Cache 能设过期、初始容量和最大容量（依赖写的是 guava 22，作者笔误成 gava）。启动时实现 Spring 回调，把主键灌进布隆过滤器；新增数据时再 `add` 一次。再往前还可以在 Nginx 加 Lua，请求先打这层本地缓存，没有再进应用的缓存或数据库。全量生成静态文件太慢时，用这一层代替「每个商品一个 HTML 文件」。

分布式锁有两条实现。Redis 用 `SETNX`，key 已存在就失败；作者更倾向直接用 Redisson。Zookeeper 用临时顺序节点，拿到最小序号的线程持锁，其他线程排队。

---

## 8. Gateway 与 Nacos

### 微服务里网关的位置

微服务是按业务把单体拆开，每个服务只做一件事，可单独部署，甚至自带数据库，服务之间用 HTTP。好处是故障面小：一个服务出问题，查这个服务的人即可，核心服务还能活。Spring Cloud 把注册发现、调用、限流熔断、配置、网关、分布式事务、链路、安全、消息这些共性问题收成一套。实现线有 Netflix 和 Alibaba 两条。不用 Spring Cloud 也能做，自己用 HTTP 客户端写死 IP 就行，但 IP 一变就得改代码，所以要把「服务名到 IP 端口」放进注册中心。

Netflix 一侧：Eureka 注册发现，Zuul 按 URL 把请求转到服务并从 Eureka 拿实例，Ribbon 在多实例间做客户端负载均衡，Feign 是声明式客户端、默认走 Ribbon，Hystrix 做熔断和监控，Dashboard 看单实例，Turbine 把各实例的监控合到一处。Alibaba 一侧：Nacos 同时做注册和配置，Sentinel 从流量切入做限流、熔断、系统保护，RocketMQ 做消息，Dubbo 做 RPC，Seata 做事务。

没有网关时，鉴权、黑名单要在每个服务写一遍，抽成公共库也要每个服务升级。请求先到网关，校验通过再转发。Gateway 的三个概念：路由决定转到哪个服务；断言是转发条件；过滤器做鉴权和改造请求，角色接近 Spring MVC 的过滤器。依赖是 `spring-cloud-starter-gateway` 加 Nacos discovery。`spring.cloud.gateway.discovery.locator.enabled=true` 之后可以用服务名访问，例如 `/mall-order/order/findOrderByUserId/1`。路由条目有全局唯一的 `id`、目标 `uri`、断言列表。

自定义断言工厂的类名必须是「名字 + RoutePredicateFactory」，例如 `CheckAuthRoutePredicateFactory`，继承 `AbstractRoutePredicateFactory`。`apply` 返回的 `Predicate` 里 `test` 返回 false 就不转发。`shortcutFieldOrder` 决定 YAML 里快捷写法的字段顺序。断言能拿到的配置在内部 `Config` 类里。

### Nacos

Nacos 同时是注册中心和配置中心。配置中心用 `spring-cloud-starter-alibaba-nacos-config`，Spring Boot 2.4 之后还要 `spring-cloud-starter-bootstrap`，否则 `bootstrap.properties` 不生效。Data Id 默认是 `应用名.扩展名`，配了 `spring.profiles.active=dev` 和 `file-extension=yaml` 就是 `nacos-config-dev.yaml`。共享配置用 `sharedConfigs`，扩展配置用 `extensionConfigs`，都可以指定 `group` 和 `refresh`。加载顺序是共享、扩展，最后才是「应用名-profile」这份，后加载的覆盖先加载的。`namespace` 和 `group` 在 bootstrap 里指定，对不上就读不到。

---

## 9. 消息队列

MQ 是暂存请求数据的容器，给分布式系统传话。不用 MQ 时，方法 1 必须等方法 2 返回，200 毫秒全算在这次请求上，方法 2 抛错方法 1 也失败。用了之后，方法 1 只负责把数据放进队列，耗时接近一次赋值，方法 2 自己监听、慢慢处理。三个作用：解耦（下游挂了上游还在）、异步（用户不用等完整链路）、削峰（一万个请求先收进队列，消费端按能力一条条做）。代价是 MQ 自己挂了整条链路不可用，系统也更复杂，所以要单独做高可用。文中点名的主流实现是 RabbitMQ、ActiveMQ、RocketMQ、Kafka。RabbitMQ 用 Erlang，协议覆盖 AMQP 等，作者认为单条数据更安全、并发能力强，但吞吐量不如 RocketMQ 和 Kafka。

### RabbitMQ

模型拆成两半：生产者只跟交换机说话，消费者只跟队列说话。队列不和交换机绑定，就永远收不到消息。入门模式和工作模式走的是默认交换机，消息会进每个绑定的队列。请求量上去之后不能这么做，否则每个队列都收到全量。于是有订阅、路由、通配符：交换机按 routing key 或模式决定进哪个队列。

Spring Boot 用 `spring-boot-starter-amqp`，配置 host 和 5672。

死信在 RabbitMQ 里叫死信交换机（DLX），因为别的 MQ 没有交换机这一层。消息变成死信的条件：队列满了之后再来的消息、消费者拒绝并且不重新入队、消息超过队列设置的 TTL。路径是：生产者到普通交换机，到普通队列；消费者拒收并丢弃；队列把消息交给死信交换机，再到另一个普通队列。队列参数 `x-dead-letter-exchange` 写死信交换机名，`x-dead-letter-routing-key` 写转过去用的 key。延迟队列用「TTL + 死信」做：消息在原队列里等到过期，再被投到真正被消费的队列。积压是消费速度跟不上生产。幂等是同一条消息可能投递多次，消费端要用业务键自己去重。集群是为了前面说的「MQ 不能单点」。

### RocketMQ

三块：Producer 发，Consumer 收，Broker 存。一台 Broker 是一台服务器，可以存多个 topic。一个 topic 的数据拆成多个 MessageQueue，队列可以散在不同 Broker 上。MessageQueue 是存储单位。消费者组是一组逻辑相同的实例，组内必须订阅完全相同的 topic，这样才能分摊和容错。

NameServer 保存所有 Broker 的信息。生产者先问 NameServer，拿到（集群里挑一个）Broker，再写到该 Broker 主节点上对应 topic 的队列。topic 默认 4 个队列。

发送方式：同步、异步、单向、顺序、广播、批量、延迟、过滤、事务。同步和异步要等 Broker 确认。单向不等确认。生产者也可以有组，主要给事务消息用：发了 half 消息的那个实例挂了，Broker 可以把确认发回同组的另一个实例。

消费分拉和推。拉是应用主动调方法。推是 Broker 收到后主动给消费端，实时性更好。集群消费是组内一条消息只给一个消费者。广播消费是组内每个消费者都收到全量。

存储：Broker 先给生产者 ack，再落盘。消费者成功消费并回 ack 之后，消息被标成已消费，不会马上删。没标成已消费就会重试。过期删除靠 `deleteWhen`（每天几点删，例子是 04）和 `fileReservedTime`（保留小时数，例子是 72）。

事务消息解决「本地事务和发消息」不能放在一个本地事务里的问题。生产者先发 half 消息，消费者看不见。Broker 确认收到后，生产者执行本地业务，返回 `COMMIT_MESSAGE`、`ROLLBACK_MESSAGE` 或未知。提交后消息才对消费者可见，回滚则丢弃。生产者在回执之前挂了，由同组其他实例或回查把事务状态补上。

### Kafka

消息消费后不删，留在日志文件里，靠配置决定文件多久删一次。概念：broker 是服务端进程；topic 是消息的最大分类；partition 是 topic 下的最小存储单位，一个分区对应一组日志文件；producer 发，consumer 收。消费者一定属于某个组，不写也会有默认组。

安装依赖 JDK 和 ZooKeeper。文中版本是 Kafka 2.4.1（Scala 2.11）。`server.properties` 里 `broker.id` 在集群中唯一，`listeners` 写给生产者和消费者的地址，`log.dir` 是日志目录，`zookeeper.connect` 指向 ZK。启动前主机名必须在 `/etc/hosts` 里映射到本机 IP，否则启动脚本会用错地址。`kafka-server-start.sh -daemon` 后台跑。主题用 `kafka-topics.sh --create`，指定副本因子和分区数，ZK 地址在 2.4 这套命令里还是 `--zookeeper`。控制台生产者连 `--broker-list`，消费者连 `--bootstrap-server`，`--from-beginning` 从头读，`--whitelist` 同时订多个主题。

单播：同一组里一条消息只被一个消费者消费，组记住这个主题已经消费到的 offset，后来的消费者从组里要 offset。多播：不同组各自消费同一主题的全量；同一组里只要有一个消费者把消息消费了，组内其他人就不再看到。作者用「组」区分这两种，而不是用两个 topic。

消费者 API 的关键配置：`group.id`；`enable.auto.commit` 默认 true，poll 到的这批处理完就提交 offset，提交之后不会再投；自动提交间隔 `auto.commit.interval.ms`。手动提交把自动提交关掉。新组或 offset 不存在时，`auto.offset.reset` 的 `latest` 只消费启动之后的新消息，`earliest` 从最早开始但之后仍按已提交的 offset 续，这和每次 `seekToBeginning` 不同。`heartbeat.interval.ms` 是心跳间隔，rebalance 方案也顺着心跳下发。`session.timeout.ms` 内没心跳就把消费者踢出组，分区分给别人，默认 10 秒。`max.poll.records` 按处理速度调，处理快可以一次多拉。`max.poll.interval.ms` 是两次 poll 的最大间隔，业务处理超过这个时间（例子 30 秒），Broker 认为这个消费者太慢，踢出组。

Rebalance 在分区数变化、或持有分区的消费者挂掉时触发。三种分配策略。Range：前 m 个消费者多分到 n+1 个分区，后面的分 n 个；再平衡时全部断开重分。Round-robin：按分区号轮询；再平衡同样全部重分。Sticky 一开始也是轮询，再平衡时尽量均匀，并且尽量保持上次的分配，只把挂掉的消费者名下的分区匀给其他人。

过程分阶段。找协调器：这个组的 offset 提交到 `__consumer_offsets` 的哪个分区，那个分区的 leader 所在 broker 就是 GroupCoordinator。加入组：消费者发 JoinGroup，协调器选第一个连上来的消费者当组长，把组里的情况交给它做分配方案。之后是同步分配方案、各消费者按方案拉自己的分区。

---

## 10. Redis

### 单线程为什么快

「单线程」指的是命令执行：100 个客户端的 `SET` 进一个队列，一条一条执行。命令内部的具体操作可以是多线程的。快是因为数据在内存里。慢命令会堵住后面所有命令。IO 用 epoll 多路复用：连接和读是分开的事件，读就绪就把命令放进队列。一条命令的路径是文件事件分配器把命令交给对应的事件处理器，处理器执行完把结果写回客户端。6.0 可以开 IO 线程：`io-threads 4` 表示 1 个 main 加 3 个 IO 线程，main 负责读写和执行命令，另外三个默认只做 write；`io-threads-do-reads yes` 才让 IO 线程也做读。

### 数据结构

所有 key 都是 SDS（simple dynamic string）。C 的 `char*` 遇到 `\0` 就截断，二进制会丢数据。SDS 自己记已用长度和分配长度，末尾仍放 `\0` 以兼容 C。3.2 之后按长度选不同头：`sdshdr5`、`sdshdr8` 等，短字符串少占头部。

RedisDb 的主体是 `dict`。`dict` 里两个 `dictht`，哈希表里是 `dictEntry` 数组。entry 存 key（指向 SDS）、value（`redisObject` 或整数、浮点）、`next` 解决哈希冲突。`redisObject` 用 4 位 type（string、set、zset 等）、4 位 encoding、LRU、引用计数，`ptr` 指向真正的值。冲突多到链表太长时，按 `used` 和 `size` 扩容，容量翻倍。扩容不一次搬完：`rehashidx` 记录进度，对某个槽做读写时顺便把这个槽搬到新表，空闲时也轮询搬。查找时新旧表都看，优先旧表。搬完把指针换过来。作者把 `ht[0]` 说成旧表、`ht[1]` 说成新表（源码下标是 0 和 1）。

List：ziplist 是一块紧凑内存，头部记能存多少、尾部地址、元素个数，每个 entry 有前一个 entry 的长度、自身长度和数据。数据一多，插入删除要挪后面的字节，所以外面套 quicklist：多个 ziplist，每个由 `quicklistNode` 串成双向链表，`quicklist` 指向头尾。单个 ziplist 太大就分裂。`list-max-ziplist-size` 控制一个节点多大，`list-compress-depth 1` 表示头尾各留 1 个节点不压缩，中间压缩。

Hash：元素少、单个值小时用 ziplist，field 和 value 各占一个 entry。超过 `hash-max-ziplist-entries`（512）或单个值超过 `hash-max-ziplist-value`（64 字节）改成 hashtable。和多个 string key 比，hash 只对一个 key 做哈希，省空间，也少触发扩容和 rehash。string 可以给每个 key 单独设过期，hash 只能给整个 key 设过期。

Set：全是整数且个数不超过 `set-max-intset-entries`（512）时用 intset，有序；否则 hashtable。

Zset：量少用 ziplist，分值和成员拆成相邻两个 entry。量大用 dict 加跳表。跳表从有序链表抽出 1、3、5 做上一层，再抽出 1、5 做更上一层，查询从顶层往下跳。头节点不存数据，只存每层的前进指针。

Geo 是地理位置，附近的人、附近的店走它的 API，底层是把经纬度编成可排序的分数放进 zset。

### 持久化、主从、哨兵

RDB：`save 60 1000` 表示 60 秒内有 1000 次写就打一次快照。快照是当时内存的全量二进制。恢复快，但内存有 8G 就要写 8G，持久化本身慢。`save` 同步，期间命令全部堵住。`bgsave` fork 子进程写盘，用写时复制，父进程继续服务，代价是多占内存。客户端也可以手动 `save` 或 `bgsave`。全部 `save` 配置删掉就关闭 RDB。

AOF：`appendonly yes`。`always` 每条命令都 fsync，最安全最慢；`everysec` 每秒一次，最多丢 1 秒；`no` 交给操作系统。缺点是重放命令慢。重写把 `INCR` 六次收成一条 `SET`。自动重写要同时满足 `auto-aof-rewrite-min-size 64mb` 和自上次重写后体积增长 100%（`auto-aof-rewrite-percentage`）。手动是 `bgrewriteaof`。

4.0 混合持久化要先开 AOF，再 `aof-use-rdb-preamble yes`。重写时前半是 RDB 二进制，重写之后新来的命令仍以 AOF 文本追加。触发了重写就是 RDB 头，没触发就还是纯 AOF。

主从：复制一份配置，改端口、pid、日志、数据目录，注释 `bind`（内网不绑网卡），`replicaof 主IP 6379`（5.0 以前是 `slaveof`），`replica-read-only yes`。第一次同步是全量：从节点发 `PSYNC`，主节点打 RDB 发过去，从节点清空再加载。快照之后、传输期间的新命令进 replication buffer，快照送完再把这些命令补上，之后靠这条长连接继续传增量。断线用部分复制：主节点留一个 `repl-backlog-size`（默认 1MB）的环形缓冲，从节点带 offset 回来，offset 还在缓冲里就续传，不在就退回全量。一个主下面挂太多从，同时全量会把主打满，这叫复制风暴。做法是让从节点再挂从节点，不要所有从都直接连主。

没有哨兵时主挂了要手工把某个从提成主。有哨兵后客户端连哨兵，哨兵再指向当前主。主挂了由哨兵重新选。`sentinel monitor mymaster 主IP 6379 2` 里的 2 是 quorum：至少两个哨兵认为主失效才判定。quorum 一般取哨兵总数的一半加一。作者注明这个判断可能脑裂。多哨兵改端口和 pid，分别启动 `redis-sentinel`。

`StringRedisTemplate` 的 key 和 value 都按字符串序列化，`set a 1` 存的就是 `a`。`RedisTemplate` 默认 JDK 序列化，存进去的不是原文，读的时候要反序列化。管道把多条命令一次发出，减少往返：`jedis.pipelined()`，循环 `incr`/`set`，`syncAndReturnAll` 拿结果。管道里一条命令写错，整批的返回里会带那条的错误。

### Cluster

哨兵切换主从时会有几秒到十几秒不可用。Cluster 没有中心节点，主从由配置自动形成，官方建议不要超过 1000 个节点。三台机器各跑两个实例，端口 8001 到 8006，一个主配一个从（`--cluster-replicas 1`）。每个实例 `cluster-enabled yes`，`cluster-config-file` 和端口对应，`cluster-node-timeout 10000`，`appendonly yes`，`protected-mode no`，注释 bind。有密码时 `requirepass` 和 `masterauth` 写成一样。节点之间除了 Redis 端口，还要放行端口加 10000 的 gossip 端口。Redis 5 之后用 `redis-cli --cluster create`，更早版本用 `redis-trib.rb`。客户端加 `-c` 才是集群模式。`cluster info`、`cluster nodes` 看状态。关闭要逐个 `shutdown`。

数据划成 16384 个槽，默认均匀分给主节点。key 的槽是 `CRC16(key) mod 16384`。客户端打到错误的节点时，节点返回重定向，客户端跳到真正持有该槽的主节点。节点之间用 gossip 交换状态：`meet` 邀请新节点，`ping` 周期交换自己的角色和集群视图，`pong` 是对 ping 和 meet 的回应，失败一类的消息用来传播节点不可达。

### 分布式锁

最朴素的做法：`SETNX` 把商品 id 设成当前线程 id，业务里再核对 value 是不是自己，做完删除。异常用 `finally` 删；进程宕了删不掉，所以要过期时间。`SETNX` 和 `EXPIRE` 分成两条命令，中间宕机仍会死锁，必须一次设上过期，Spring 里是 `setIfAbsent(key, value, timeout, unit)`。

过期时间太短会出第二个问题：A 还在执行，锁过期，B 拿到锁，A 结束时把 B 的锁删了。删除前要比较 value 是不是自己的线程 id。过期本身仍会自动删，所以持锁期间用定时任务续期：key 还在就延长过期时间。Redisson 把「设过期、比较 value、续期、Lua 保证原子」包进 `RLock.lock()` / `unlock()`。没抢到的线程循环重试。

主从架构下这把锁仍不可靠：锁写在主上，还没同步到从，主宕了，新主没有这把锁。作者认为把这个问题做成强一致会把性能打下来，高并发下改成分段锁：库存 1000 拆成 `sp_001_1` 到 `sp_001_10`，每段 100，散到不同节点，各段自己加锁。

### 缓存异常

穿透：查不存在的 key，Redis 没有，请求全落到 MySQL。同一个 key 可以缓存空值；攻击者每次换 key 就失效。布隆过滤器说不存在就一定不存在。击穿和雪崩的处理见第 7 节，和商品架构那几篇是同一套结论。

---

## 11. MySQL

### 索引结构

二叉树有序插入会退化成链表，树很高。红黑树会自己平衡，有序数据仍然偏高。哈希按桶定位，等值快，范围扫描慢。B 树一个节点存多个有序键和数据位置，节点满了再分裂。B+ 树再进一步：非叶子只存键，叶子才有全部索引字段，相邻叶子用指针串起来，范围扫描不用回到根。一棵树通常不超过 4 层。InnoDB 一页默认 16KB，`show global status like 'Innodb_page_size'` 可查。

MyISAM（作者写成 MylSAM）索引和数据分文件。B+ 树叶子存的是磁盘地址。InnoDB 是聚簇索引，主键索引的叶子就是整行。非主键索引的叶子存索引列加主键值，查到之后再回主键树拿整行，这就是回表。不建主键时，InnoDB 会找一个不重复的列，找不到就造隐藏列。主键用自增整数：顺序插入多在最右边追加，页满了直接分裂出新页。随机主键要在页中间插入，插入前还要为排序挪动，页满就频繁分裂。联合索引按定义顺序排序，第一个字段相同才看第二个。除最左前缀之外，后面的列在索引里不是独立有序的，所以查询也要从左用到右。

### 一条 SQL 怎么执行，Explain 怎么看

`EXPLAIN` 打在语句前面，看优化器打算怎么跑。`EXPLAIN EXTENDED` 之后再 `SHOW WARNINGS`，能看到改写后的语句，并多一列 `filtered`，和别的表连接时用 `rows * filtered / 100` 估计。`EXPLAIN PARTITIONS` 多分区列。5.7 后期这些并进了普通 `EXPLAIN`。

`id` 越大越先执行，相同则按出现顺序，NULL 最后。`select_type`：`SIMPLE` 简单查询；`PRIMARY` 最外层；`SUBQUERY` 是 select 列表里的子查询；`DERIVED` 是 from 里的子查询，会落成临时表；`UNION` 是 union 里第二条及以后。`type` 从好到差：`system` > `const` > `eq_ref` > `ref` > `range` > `index` > `ALL`。`NULL` 表示优化阶段就算出结果，执行时不再碰表，例如索引列上的 `MIN`。`const` 和 `system` 都是主键或唯一键命中一行，`system` 走主键，`const` 走二级索引所以多一次回表。`eq_ref` 是连接时被驱动表按唯一键最多命中一行。`ref` 不是唯一索引，可能多行。`range` 是索引上的范围（`IN`、`BETWEEN`、比较）。`index` 是扫整棵索引的叶子，不是从根做二分。`ALL` 是全表。`possible_keys` 是候选，`key` 是实际用的。`key_len` 看出联合索引用了前几列。utf8 下汉字 3 字节，`char(n)` 是 `3n`，`varchar(n)` 是 `3n+2`（2 字节记长度），允许 NULL 再加 1 字节。索引最长 768 字节，超长的字符串只会索引前缀。

### 优化器什么时候放弃索引

联合索引 `(name, age, position)` 上，`name > 'am' AND age = 22 AND position = '12'` 往往不走索引：范围之后还要回表，优化器认为回表次数太多，不如全表。改成只 select 这三个列，变成覆盖索引，就不回表了。也可以 `FORCE INDEX`，但不保证更快。比较前关掉查询缓存（5.6 及更早）：`query_cache_size` 和 `query_cache_type` 设 0。

`IN` 和 `OR` 在数据量大时走索引，数据很少时全表。`LIKE 'LiLei%'` 一般走索引，并且 5.6 之后有索引下推：在联合索引里用 name 定位时，顺便用索引里的 age、position 过滤，再回表。5.6 之前只按 name 找出主键，条件里其余列回到表上比。范围条件没有下推，作者的理解是范围结果集太大，逐行比较后面的列不划算。

`SELECT * ... WHERE name > 'a'` 不走索引，`SELECT name, age, position ... WHERE name > 'a'` 走覆盖索引。原因同样是：`*` 一定回表，范围又大。用 optimizer trace 看选择过程：`SET SESSION optimizer_trace="enabled=on"`，执行语句，再查 `information_schema.OPTIMIZER_TRACE`。JSON 里先是 `join_preparation`（把语句展开），再是 `join_optimization`（条件处理、选索引、算代价）。

### 分页、Join、count

`LIMIT 10000, 10` 会扫过前 10010 行再丢掉前 10000。主键连续时改成 `WHERE id > 90000 LIMIT 5`。按非主键排序时，先在子查询里只取排序后的主键（走索引、少回表），再和原表按主键 join 拿整行。走不走索引由优化器比代价，作者把这个代价值写成 cost。

连接算法两种。NLJ 是被驱动表走了索引：驱动表一行，到被驱动表的索引上等值查找，通常再读一行。`INNER JOIN` 里谁是驱动表看 explain，不一定是 SQL 里写在左边的那张。小表做驱动表：100 行驱动、被驱动表有索引，大约 100 次索引查找；反过来用 10000 行驱动，循环次数跟着放大。BNL 是没走索引：驱动表按 `join_buffer` 分块装进内存，每块和被驱动表全表比，代价接近两表行数相乘。缓冲装不下就分段。关联表别超过三张，连接字段要有索引，小表驱动。`STRAIGHT_JOIN` 强制左边驱动右边。

`IN` 适合外表大、子查询结果小，语义是先拿出子查询的 id 再去外表查。`EXISTS` 适合外表小，对外表每一行去内表探一下。有索引时 `EXISTS` 常可以改写成 join。

`count(*)` 和 `count(1)` 接近，都是碰到行就在内存里加一。有索引时 `count(字段)` 扫二级索引，比扫主键（聚簇、行更宽）便宜，所以 `count(*) ≈ count(1) > count(字段) > count(主键)`。字段没索引时 `count(字段)` 落到全表，顺序变成 `count(*) ≈ count(1) > count(主键) > count(字段)`。MyISAM 自己维护不带 WHERE 的总行数，`SHOW TABLE STATUS` 能看到一个不精确的估计。总数也可以放 Redis，但事务回滚时缓存不会自己跟着回滚。

### 隔离级别、锁、MVCC

并发问题：脏写是两个事务都读到 20，一个减 2 写成 18，另一个按自己读到的 20 减 3 写成 17，正确应是 15。脏读是读到别人没提交的修改。不可重复读是别人提交后，本事务再次读取值变了。幻读在笔记里的例子是：A 插入的行 B 的查询看不到，但 B 的 update 能碰到这行。

ACID：原子性是事务里的语句同成同败；一致性是数据上同成同败（作者把两者分成「操作」和「数据」两个角度看）；隔离性是只看到事务开始那一刻允许看到的数据；持久性是提交后重启不丢。

隔离级别：读未提交什么都不解决；读已提交解决脏读；可重复读再解决不可重复读；串行化连幻读一起解决。`SET tx_isolation` 取值 `read-uncommitted`、`read-committed`、`repeatable-read`。

锁是让后来的事务等。表锁锁整张表，行锁只锁碰到的行。乐观锁用版本列，更新时带上读到的版本，版本不符就走失败逻辑。悲观锁是会等的锁。写锁（X）期间别人不能读不能改；读锁（S）期间别人能读不能改。表锁：`LOCK TABLES t READ|WRITE`，`SHOW OPEN TABLES` 看，`UNLOCK TABLES` 放。引擎差别：InnoDB 有事务和行锁，select 加行读锁，insert、update、delete 加行写锁。MyISAM 没有事务，select 前加表读锁，写语句前加表写锁。

MVCC 让读已提交和可重复读不用把每行都锁到串行。两部分：undo 版本链和 Read View。行上有隐藏的 `trx_id` 和 `roll_pointer`。每次更新把旧行推进 undo，新版本的 `roll_pointer` 指向旧版本，形成链。只有修改进版本链。事务执行 select 时生成 Read View。可重复读在第一次 select 时记下当时尚未提交的事务 id，之后本事务都用这一个视图，所以别人后来的提交看不见。没有 MVCC 就只能退回串行化，每行加锁等待。

Buffer Pool 是 InnoDB 把数据页放进内存的缓存，避免每次都读盘。和 MVCC 放在同一篇里讲，是因为版本链和页都在内存结构里周转。

### 分库分表与 ShardingSphere

集群、高可用、分库分表是容量和可用性三条线，不要混成一个词。数据量和单机磁盘、单机连接数到顶之后才拆。ShardingJDBC 是客户端分片，应用依赖它，SQL 在应用里改写后打到各个库。ShardingProxy 是独立进程，对应用假装自己是一个 MySQL，分片规则在代理上配。原理篇把扩展点收在 SPI：分片算法、解析、路由按接口插进去，而不是改核心。使用篇是把逻辑表、真实数据源和分片键配起来，让一条逻辑 SQL 落到多个物理表。

---

## 原文

Docker、Compose、Kubernetes

- [Docker一](https://blog.csdn.net/qq_36301061/article/details/122355093)
- [Docker二](https://blog.csdn.net/qq_36301061/article/details/122372884)
- [Docker Compose](https://blog.csdn.net/qq_36301061/article/details/122387984)
- [Kubernetes（K8S）一](https://blog.csdn.net/qq_36301061/article/details/122444250)
- [Kubernetes（K8S）二](https://blog.csdn.net/qq_36301061/article/details/122461379)

中台、秒杀

- [中台的介绍](https://blog.csdn.net/qq_36301061/article/details/122293804)
- [秒杀场景一](https://blog.csdn.net/qq_36301061/article/details/122246736)
- [秒杀场景二](https://blog.csdn.net/qq_36301061/article/details/122271706)

Elasticsearch

- [ElasticSearch简说](https://blog.csdn.net/qq_36301061/article/details/117298770)
- [ElasticSearch的DSL语言](https://blog.csdn.net/qq_36301061/article/details/117333129)
- [ElasticSearch搜索技术与集群架构原理](https://blog.csdn.net/qq_36301061/article/details/117385699)
- [bucket与metric聚合](https://blog.csdn.net/qq_36301061/article/details/117788635)
- [ElasticSearch进阶与Java API整合ES](https://blog.csdn.net/qq_36301061/article/details/118338583)
- [java整合ES](https://blog.csdn.net/qq_36301061/article/details/118368518)
- [如何使用es实现搜索功能1](https://blog.csdn.net/qq_36301061/article/details/122201256)
- [如何使用es实现搜索功能2](https://blog.csdn.net/qq_36301061/article/details/122225475)

并发与高并发架构

- [并发架构一](https://blog.csdn.net/qq_36301061/article/details/122096670)
- [并发架构二](https://blog.csdn.net/qq_36301061/article/details/122148163)
- [并发架构三](https://blog.csdn.net/qq_36301061/article/details/122161548)
- [并发设计模式](https://blog.csdn.net/qq_36301061/article/details/122074919)（122074909、122074899、122074880 与这篇正文相同）
- [JMM模型一](https://blog.csdn.net/qq_36301061/article/details/121558671)
- [JMM模型二](https://blog.csdn.net/qq_36301061/article/details/121626922)
- [java线程](https://blog.csdn.net/qq_36301061/article/details/121664323)
- [CAS与原子类](https://blog.csdn.net/qq_36301061/article/details/121687010)
- [synchronized（一）](https://blog.csdn.net/qq_36301061/article/details/121754654)
- [synchronized（二）](https://blog.csdn.net/qq_36301061/article/details/121779379)
- [ReentrantLock](https://blog.csdn.net/qq_36301061/article/details/121801532)
- [Semaphore、CountDownLatch](https://blog.csdn.net/qq_36301061/article/details/121844417)
- [CyclicBarrier](https://blog.csdn.net/qq_36301061/article/details/121864714)
- [ReentrantReadWriteLock](https://blog.csdn.net/qq_36301061/article/details/121941571)
- [阻塞队列 ArrayBlockingQueue 与 LinkedBlockingQueue](https://blog.csdn.net/qq_36301061/article/details/121963342)
- [阻塞队列 SynchronousQueue、LinkedTransferQueue](https://blog.csdn.net/qq_36301061/article/details/121987265)
- [Fork/Join](https://blog.csdn.net/qq_36301061/article/details/122006940)
- [Callable、Future、FutureTask、Disruptor](https://blog.csdn.net/qq_36301061/article/details/122018891)
- [手写线程池（一）](https://blog.csdn.net/qq_36301061/article/details/123514922)
- [java实现锁的思想](https://blog.csdn.net/qq_36301061/article/details/123586805)
- [偏向锁源码](https://blog.csdn.net/qq_36301061/article/details/123809761)

Gateway、Nacos

- [spring-cloud 网关Gateway](https://blog.csdn.net/qq_36301061/article/details/121041360)
- [Nacos配置中心](https://blog.csdn.net/qq_36301061/article/details/120934108)
- [Spring-Cloud-Alibaba-Nacos](https://blog.csdn.net/qq_36301061/article/details/120829037)

消息队列

- [认识与简单使用kafka](https://blog.csdn.net/qq_36301061/article/details/119614227)
- [kafka原理1与使用](https://blog.csdn.net/qq_36301061/article/details/119674054)
- [kafka原理2](https://blog.csdn.net/qq_36301061/article/details/119716209)
- [RabbitMQ入门](https://blog.csdn.net/qq_36301061/article/details/119010899)
- [RabbitMQ的订阅模式、路由模式、通配符模式](https://blog.csdn.net/qq_36301061/article/details/119044099)
- [rabbitMQ高级特性](https://blog.csdn.net/qq_36301061/article/details/119065305)
- [死信队列、延迟队列、消息积压、幂等性、集群](https://blog.csdn.net/qq_36301061/article/details/119144069)
- [rocketmq搭建](https://blog.csdn.net/qq_36301061/article/details/119222564)
- [rocketmq的消息类型](https://blog.csdn.net/qq_36301061/article/details/119257663)
- [rocketmq基本概念](https://blog.csdn.net/qq_36301061/article/details/119330100)
- [rocketmq基本概念2](https://blog.csdn.net/qq_36301061/article/details/119387047)
- [rocketMQ事务消息](https://blog.csdn.net/qq_36301061/article/details/119303903)
- [rocketmq源码](https://blog.csdn.net/qq_36301061/article/details/119490844)

Redis

- [Redis的单线程跟高性能](https://blog.csdn.net/qq_36301061/article/details/118410754)
- [持久化、主从、哨兵](https://blog.csdn.net/qq_36301061/article/details/118465566)
- [redis cluster](https://blog.csdn.net/qq_36301061/article/details/118499235)
- [redis分布式锁](https://blog.csdn.net/qq_36301061/article/details/118531003)
- [redis k-v设计、sds](https://blog.csdn.net/qq_36301061/article/details/118559361)
- [redis核心数据结构底层原理](https://blog.csdn.net/qq_36301061/article/details/118633638)
- [redis在高并发场景下的问题](https://blog.csdn.net/qq_36301061/article/details/118676945)

MySQL

- [mysql索引数据结构](https://blog.csdn.net/qq_36301061/article/details/116589169)
- [Explain工具跟一条sql怎么执行的](https://blog.csdn.net/qq_36301061/article/details/116657052)
- [MySql索引优化一](https://blog.csdn.net/qq_36301061/article/details/116718943)
- [MySql索引优化二](https://blog.csdn.net/qq_36301061/article/details/116763294)
- [事务隔离级别跟锁机制](https://blog.csdn.net/qq_36301061/article/details/116854299)
- [MVCC机制与Buffer Pool](https://blog.csdn.net/qq_36301061/article/details/116904873)
- [MySQL 集群、高可用、分库分表](https://blog.csdn.net/qq_36301061/article/details/119762388)
- [ShardingJDBC](https://blog.csdn.net/qq_36301061/article/details/119811554)
- [ShardingSphere原理 SPI](https://blog.csdn.net/qq_36301061/article/details/119866801)
- [ShardingProxy](https://blog.csdn.net/qq_36301061/article/details/119909134)

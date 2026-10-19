# Docker

## 查看显卡信息：
amd-smi static

## 动态监控
amd-smi monitor -q
按 Ctrl + C 可以退出监控界面

## 创建并激活虚拟环境
python3 -m venv ~/amdsmi-venv
source ~/amdsmi-venv/bin/activate
python check_gpu.py
## 退出
deactivate

## 开机启动docker
docker compose up -d 

## 本地使用mineru
mineru -p D:\python\doc\hak180产品安全手册.pdf -o D:\python\output --backend pipeline

## 权限文件
sudo tee /etc/docker/daemon.json <<-'EOF'
{
  "registry-mirrors": [
    "https://docker.1ms.run",
    "https://docker.xuanyuan.me",
    "https://docker.m.daocloud.io",
    "https://dockerpull.org",
    "https://docker.1panel.live"
  ]
}
EOF

## 普通文件
code docker-compose.yml

## powershell里
循环配置端口转发（目标 IP 为你刚才查到的 192.168.3.17）
$ports = @(7000, 9000, 9002, 9003, 19530, 9091, 27017)
foreach ($port in $ports) {
    netsh interface portproxy add v4tov4 listenport=$port listenaddress=0.0.0.0 connectport=$port connectaddress=192.168.3.17
    Write-Host "已将 Windows 端口 $port 转发至 192.168.3.17:$port" -ForegroundColor Green
}

## gpu版本备份
services:
  etcd:
    container_name: milvus-etcd
    image: quay.io/coreos/etcd:v3.5.18
    environment:
      - ETCD_AUTO_COMPACTION_MODE=revision
      - ETCD_AUTO_COMPACTION_RETENTION=1000
      - ETCD_QUOTA_BACKEND_BYTES=4294967296
      - ETCD_SNAPSHOT_COUNT=50000
    volumes:
      - ${DOCKER_VOLUME_DIRECTORY:-.}/volumes/etcd:/etcd
    command: etcd -advertise-client-urls=http://127.0.0.1:2379 -listen-client-urls http://0.0.0.0:2379 --data-dir /etcd
    healthcheck:
      test: ["CMD", "etcdctl", "endpoint", "health"]
      interval: 30s
      timeout: 20s
      retries: 3

  minio:
    container_name: milvus-minio
    image: minio/minio:RELEASE.2023-03-20T20-16-18Z
    environment:
      MINIO_ACCESS_KEY: minioadmin
      MINIO_SECRET_KEY: minioadmin
    ports:
      - "9003:9001"
      - "9002:9000"
    volumes:
      - ${DOCKER_VOLUME_DIRECTORY:-.}/volumes/minio:/minio_data
    command: minio server /minio_data --console-address ":9001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 30s
      timeout: 20s
      retries: 3

  standalone:
    container_name: milvus-standalone
    image: milvusdb/milvus:v2.5.5-gpu
    command: ["milvus", "run", "standalone"]
    security_opt:
    - seccomp:unconfined
    environment:
      ETCD_ENDPOINTS: etcd:2379
      MINIO_ADDRESS: minio:9000
      USE_GPU: "true"
    volumes:
      - ${DOCKER_VOLUME_DIRECTORY:-.}/volumes/milvus:/var/lib/milvus
    devices:
      - /dev/kfd:/dev/kfd
      - /dev/dri:/dev/dri
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9091/healthz"]
      interval: 30s
      start_period: 90s
      timeout: 20s
      retries: 3
    ports:
      - "19530:19530"
      - "9091:9091"
    depends_on:
      - "etcd"
      - "minio"
      
  attu:
    container_name: milvus-attu
    image: zilliz/attu:v2.5.10
    environment:
      - MILVUS_URL=standalone:19530
      - ATTU_LOG_LEVEL=info
      - SERVER_NAME=auto_gui
      - SERVER_PORT=7000
    ports:
      - "7000:7000"
    volumes:
      - /root/milvus:/app/tls
    depends_on:
      - standalone

networks:
  default:
    name: milvus
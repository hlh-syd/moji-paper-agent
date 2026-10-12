# 云 GPU 执行计划（humanize-4b 蒸馏，总费用约 ¥10）

## 平台选择（任选其一）
| 平台 | 推荐卡 | 价格 | 说明 |
|---|---|---|---|
| AutoDL（autodl.com） | RTX 4090 24GB | ~¥2/h | 最常用，步骤如下 |
| 恒源云 | 4090 / A5000 | ~¥2/h | 流程相同 |
| 仙宫云 / 揽睿 | 4090 | 类似 | 流程相同 |

## AutoDL 详细步骤

### 1. 开实例
- 注册 → 充值 ¥15 → 算力市场
- 选 **RTX 4090（24GB）**，地区任选（有货即可）
- 镜像选：**PyTorch 2.x / CUDA 12.x / Python 3.10+**（基础镜像即可，已带 torch）
- 创建后记住：**SSH 登录指令和密码**（控制台可见）

### 2. 上传迁移包（三选一）
```bash
# 方式 A：网页版 JupyterLab 直接拖拽上传 humanize_cloud_gpu.zip 到 /root/autodl-tmp/
# 方式 B：scp（本机执行，端口和地址替换成你实例的）
scp -P 12345 humanize_cloud_gpu.zip root@region-xx.autodl.com:/root/autodl-tmp/
# 方式 C：AutoDL 网盘中转：本地上传到阿里云盘/百度网盘 → 实例内"文件存储"导入
```
> ⚠️ 数据放 `/root/autodl-tmp/`（数据盘，关机不丢且空间大）；系统盘只有 30GB

### 3. 运行（实例终端里三行）
```bash
cd /root/autodl-tmp && unzip humanize_cloud_gpu.zip && cd distill_humanize
nohup bash run_all.sh > run.log 2>&1 &    # 后台跑，断开SSH也不停
tail -f run.log                           # 随时看进度
```
- 全程自动：GPU检测 → 依赖 → 基座下载8GB → 冒烟 → 训练3375步 → 合并 → GGUF量化
- 预计总时长：**4090 约 4~5 小时**（含下载基座~20min + 训练~3.5h + 转换~20min）
- 中途断了不慌：重新执行 `bash run_all.sh` 会跳过已完成部分；训练本身每个 epoch 有 checkpoint

### 4. 下载产物
进度到 `✅ 完成！` 后，下载 **`humanize-4b-q4km.gguf`（~2.5GB）**：
```bash
# 本机执行（或网页 JupyterLab 右键下载）
scp -P 12345 root@region-xx.autodl.com:/root/autodl-tmp/distill_humanize/humanize-4b-q4km.gguf .
```

### 5. 关机！
控制台点**关机**（无卡模式不计费/按量停止），费用定格 ~¥9。

## 常见问题
| 问题 | 处置 |
|---|---|
| 下载基座慢/401 | run_all.sh 已设 hf-mirror + 禁Xet，无需操作；仍慢则重跑 |
| 训练 OOM（4090 不会） | 编辑 run_all.sh 里训练行加 `--seqlen 768` |
| 实例中途断网 | nohup 后台任务不受影响，重连后 `tail run.log` |
| 只想重跑量化 | `SKIP_TRAIN=1 bash run_all.sh` |

## 产物回本机后
把 `humanize-4b-q4km.gguf` 放到 `论文智能体/distill_humanize/`，告诉 ZCode"模型回来了"：
执行 `ollama create humanize-4b -f Modelfile` → 接入深度去AI引擎 → 朱雀前后对比验收。

#!/usr/bin/env bash
# 测试房实弹运行脚本:打满 N 轮(房间 M=10 → 每轮 10 场,~4 分钟/轮),
# 每轮结束后自动用免认证数据端点做引擎-平台 replay 对账。
#
# 用法:
#   scripts/run_room.sh                 # 默认:BC 策略打 1 轮
#   scripts/run_room.sh 5               # 打 5 轮(50 场,攒数据)
#   scripts/run_room.sh 5 bot           # 用启发式 bot 策略
#   CONFIG=local/platform.json scripts/run_room.sh 3
#
# 前提:local/platform.json 已配好 4 个测试房令牌(见 README.md)。
set -euo pipefail
cd "$(dirname "$0")/.."

ROUNDS="${1:-1}"                 # 轮数(每轮 = M 场)
STRATEGY="${2:-policy}"          # policy | bot | random
CKPT="${CKPT:-runs/bc0/best.pt}"
CONFIG="${CONFIG:-local/platform.json}"

# 从令牌发现房间 id(报名令牌绑定锦标赛)
ROOM=$(python3 - "$CONFIG" <<'PYEOF'
import sys
sys.path.insert(0, ".")
from mj.platform.api import Api
from mj.platform.config import load_config
cfg = load_config(sys.argv[1])
api = Api(cfg["server"], next(iter(cfg["tokens"].values())))
tid = api.me().get("tournament_id", "")
if not tid:
    raise SystemExit("令牌未绑定锦标赛:请用门户测试房派发的参赛令牌")
print(tid)
PYEOF
)
echo "房间: $ROOM | 轮数: $ROUNDS | 策略: $STRATEGY | checkpoint: $CKPT"

for i in $(seq 1 "$ROUNDS"); do
    echo ""
    echo "===== 第 $i/$ROUNDS 轮 ====="
    # 一轮 = --games M(M=10 → 10 场并发);打完自动 re-ready 交由下一轮
    python3 -m mj.platform.runner --config "$CONFIG" \
        --strategy "$STRATEGY" --ckpt "$CKPT" --games 10
    # 赛后对账(免认证数据端点,引擎重放 + 结算比对)
    python3 -m mj.replay --room "$ROOM"
done

echo ""
echo "全部完成。局数与事件流可随时复查:"
echo "  python3 -m mj.replay --room $ROOM --all --out report.json"

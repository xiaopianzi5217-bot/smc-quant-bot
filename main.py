"""SMC Bot 主入口 - 使用 V11 Institutional Runner"""
import os
import sys
from pathlib import Path

# 添加项目根目录到 PYTHONPATH
project_root = Path(__file__).parent.absolute()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


def _ensure_env():
    """确保环境变量加载，包括 .env 文件和 Windows 系统环境变量兜底"""
    # 1. 尝试加载 .env 文件
    env_path = project_root / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and not os.getenv(key):
                    os.environ[key] = val

    # 2. 关键环境变量兜底：从 Windows 系统环境变量读取（PowerShell 有时不继承）
    _essential_vars = [
        ("TG_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"),
        ("TG_CHAT_ID", "TELEGRAM_CHAT_ID"),
        ("WX_BOT_KEY", "PUSHPLUS_TOKEN"),
        ("EXCHANGE_API_KEY", "BITGET_API_KEY"),
        ("EXCHANGE_API_SECRET", "BITGET_SECRET"),
        ("EXCHANGE_PASSWORD", "BITGET_PASSWORD"),
        ("HF_TOKEN", None),
    ]
    for primary, alt in _essential_vars:
        if not os.getenv(primary):
            # 尝试从系统环境变量中读取（Windows 用户级环境变量）
            try:
                import subprocess
                result = subprocess.run(
                    ["cmd", "/c", f"echo %{primary}%"],
                    capture_output=True, text=True, timeout=3
                )
                val = result.stdout.strip()
                if val and val != f"%{primary}%":
                    os.environ[primary] = val
            except Exception:
                pass
            # 尝试备选名
            if not os.getenv(primary) and alt:
                try:
                    import subprocess
                    result = subprocess.run(
                        ["cmd", "/c", f"echo %{alt}%"],
                        capture_output=True, text=True, timeout=3
                    )
                    val = result.stdout.strip()
                    if val and val != f"%{alt}%":
                        os.environ[primary] = val
                except Exception:
                    pass


from runner.v11_institutional_runner import run_once, load_config
from ops.env_config import load_runtime_config
import asyncio


async def main():
    print("🚀 SMC Bot V54+ 启动中...")
    _ensure_env()

    # 加载配置
    cfg = load_config()
    print(f"✅ 配置加载完成，版本: {cfg.get('version', 'V54')}")

    # 运行一次扫描
    try:
        results = run_once(cfg=cfg)
        print(f"✅ 单次扫描完成，信号数: {len(results)}")
    except Exception as e:
        print(f"❌ 运行异常: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())

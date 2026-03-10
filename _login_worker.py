# -*- coding: utf-8 -*-
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("mijiaAPI").setLevel(logging.DEBUG)

try:
    from mijiaAPI import mijiaAPI
except ImportError as e:
    print(f"ERROR: 缺少依赖库 - {e}", flush=True)
    sys.exit(1)

def main():
    if len(sys.argv) < 2:
        print("ERROR: 未指定 auth.json 路径", flush=True)
        sys.exit(1)
    
    auth_path = sys.argv[1]
    print("[WORKER] 开始初始化认证环境。", flush=True)
    
    try:
        Path(auth_path).parent.mkdir(parents=True, exist_ok=True)
        api = mijiaAPI(auth_path)
        print("[WORKER] API 实例已创建，正在请求小米服务器...", flush=True)
        api.login() 
        print("\n[WORKER_SUCCESS] 授权完毕。", flush=True)
    except Exception as e:
        print(f"\n[WORKER_ERROR] 登录流程失败: {type(e).__name__}", flush=True)
        sys.exit(1)

if __name__ == "__main__":
    main()

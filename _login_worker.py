# -*- coding: utf-8 -*-
import sys
import os

try:
    from mijiaAPI import mijiaAPI
except ImportError:
    print("ERROR: mijiaAPI 库缺失，请配置 requirements.txt", flush=True)
    sys.exit(1)

def main():
    if len(sys.argv) < 2:
        print("ERROR: 未指定 auth.json 路径", flush=True)
        sys.exit(1)
    
    auth_path = sys.argv[1]
    try:
        os.makedirs(os.path.dirname(auth_path), exist_ok=True)
        api = mijiaAPI(auth_path)
        # login() 负责输出二维码链接至 stdout
        api.login() 
        print("\n[WORKER_SUCCESS] 授权完毕。", flush=True)
    except Exception as e:
        print(f"\n[WORKER_ERROR] {e}", flush=True)
        sys.exit(1)

if __name__ == "__main__":
    main()

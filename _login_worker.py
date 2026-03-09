# -*- coding: utf-8 -*-
"""
米家登录沙盒进程。
专用于隔离 mijiaAPI 的终端打印副作用，防止污染主进程的 stdout。
用法: python _login_worker.py <auth.json的绝对路径>
"""
import sys

try:
    from mijiaAPI import mijiaAPI
except ImportError:
    print("ERROR: 未安装 mijiaAPI", flush=True)
    sys.exit(1)

def main():
    if len(sys.argv) < 2:
        print("ERROR: 未提供 auth.json 路径", flush=True)
        sys.exit(1)
        
    auth_path = sys.argv[1]
    
    try:
        api = mijiaAPI(auth_path)
        api.login() 
        print("\n[LOGIN_SUCCESS] 授权已完成或凭证原本就有效。", flush=True)
    except Exception as e:
        print(f"\n[LOGIN_ERROR] {e}", flush=True)
        sys.exit(1)

if __name__ == "__main__":
    main()

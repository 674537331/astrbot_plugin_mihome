# -*- coding: utf-8 -*-
import os
import json
from miservice import MiAccount, MiIOService

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig

@register("astrbot_plugin_mihome_control", "RyanVaderAn", "米家设备云端控制插件 (基于 MiService)", "v2.0")
class MiHomeControlPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.username = self.config.get("mi_username", "")
        self.password = self.config.get("mi_password", "")
        
        # 将小米的 Token 缓存文件存放在 AstrBot 的插件数据目录下，防止丢失和重复登录触发风控
        self.token_store_path = os.path.join(self.context.get_data_dir(), "mi_token_cache.json")

    async def _get_mi_service(self) -> MiIOService:
        """初始化小米账号并获取云端服务实例"""
        if not self.username or not self.password:
            raise ValueError("请先在 AstrBot WebUI 面板中配置你的小米账号和密码。")
            
        # MiAccount 会自动处理缓存。如果缓存有效，它不会去请求小米登录接口
        account = MiAccount(self.username, self.password, self.token_store_path)
        # xiaomiio 是标准设备控制接口的 client_id
        await account.login('xiaomiio')
        return MiIOService(account)

    @filter.command("刷新米家")
    async def refresh_mihome_devices(self, event: AstrMessageEvent):
        """
        指令：/刷新米家
        用于测试 MiService 连通性，并打印设备列表。
        """
        yield event.plain_result("正在通过 MiService 连接小米云端，请稍候...")
        
        try:
            # 1. 获取服务实例
            service = await self._get_mi_service()
            
            # 2. 拉取设备列表
            logger.info("MiService 登录/鉴权成功，正在拉取设备...")
            devices = await service.device_list()
            
            if not devices:
                yield event.plain_result("拉取成功，但你的账号下没有绑定任何支持 MIoT 协议的设备。")
                return
                
            # 3. 整理设备信息
            result_texts = [f"✅ MiService 成功找到 {len(devices)} 个设备：\n"]
            
            for idx, dev in enumerate(devices):
                name = dev.get('name', '未知设备')
                model = dev.get('model', '未知型号')
                did = dev.get('did', '未知DID')
                is_online = "在线" if dev.get('isOnline') else "离线"
                
                info = f"{idx + 1}. 【{name}】\n  - 型号: {model}\n  - DID: {did}\n  - 状态: {is_online}\n"
                result_texts.append(info)
                
            final_text = "\n".join(result_texts)
            if len(final_text) > 1000:
                final_text = final_text[:997] + "..."
                
            yield event.plain_result(final_text)
            
        except Exception as e:
            logger.error(f"MiService 交互异常: {str(e)}", exc_info=True)
            yield event.plain_result(f"获取设备时发生异常：{str(e)}")

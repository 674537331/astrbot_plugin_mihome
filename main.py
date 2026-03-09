# -*- coding: utf-8 -*-
import os
import json
from miservice import MiAccount, MiIOService

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

@register("astrbot_plugin_mihome", "RyanVaderAn", "米家设备云端控制插件 (基于 MiService)", "v2.4")
class MiHomeControlPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        # 接收 WebUI 传入的配置
        self.config = config
        self.username = self.config.get("mi_username", "")
        self.password = self.config.get("mi_password", "")
        
        # 安全解析 WebUI 传入的 JSON 格式设备映射表
        raw_map = self.config.get("device_map", "{}")
        if isinstance(raw_map, str):
            try:
                self.device_map = json.loads(raw_map)
            except Exception as e:
                logger.error(f"解析 device_map 失败，请检查 JSON 格式: {e}")
                self.device_map = {}
        else:
            self.device_map = raw_map if isinstance(raw_map, dict) else {}
        
        # 严格遵循官方持久化数据存储规范
        plugin_data_path = get_astrbot_data_path() / "plugin_data" / self.name
        os.makedirs(plugin_data_path, exist_ok=True)
        
        # 将小米的 Token 缓存文件安全地存放在规范目录下
        self.token_store_path = os.path.join(plugin_data_path, "mi_token_cache.json")

    async def _get_mi_service(self) -> MiIOService:
        """初始化小米账号并获取云端服务实例"""
        if not self.username or not self.password:
            raise ValueError("请先在 AstrBot WebUI 面板中配置你的小米账号和密码。")
            
        account = MiAccount(self.username, self.password, self.token_store_path)
        await account.login('xiaomiio')
        return MiIOService(account)

    @filter.command("刷新米家")
    async def refresh_mihome_devices(self, event: AstrMessageEvent):
        """
        用于测试 MiService 连通性，并打印设备列表。
        """
        yield event.plain_result("正在通过 MiService 连接小米云端，请稍候...")
        
        try:
            service = await self._get_mi_service()
            logger.info("MiService 登录/鉴权成功，正在拉取设备...")
            devices = await service.device_list()
            
            if not devices:
                yield event.plain_result("拉取成功，但你的账号下没有绑定任何支持 MIoT 协议的设备。")
                return
                
            result_texts = [f"✅ MiService 成功找到 {len(devices)} 个设备：\n"]
            for idx, dev in enumerate(devices):
                name = dev.get('name', '未知设备')
                model = dev.get('model', '未知型号')
                did = dev.get('did', '未知DID')
                is_online = "在线" if dev.get('isOnline') else "离线"
                
                info = f"{idx + 1}. 【{name}】\n  - 型号: {model}\n  - DID: {did}\n  - 状态: {is_online}\n"
                result_texts.append(info)
                
            final_text = "\n".join(result_texts)
            # 防止消息过长被某些平台截断
            if len(final_text) > 1000:
                final_text = final_text[:997] + "..."
                
            yield event.plain_result(final_text)
            
        except Exception as e:
            logger.error(f"MiService 交互异常: {str(e)}", exc_info=True)
            yield event.plain_result(f"获取设备时发生异常：{str(e)}")

    @filter.command("控制米家")
    async def control_mihome_device(self, event: AstrMessageEvent, device_name: str, action: str):
        """
        指令：/控制米家 [设备别名] [开/关]
        示例：/控制米家 风扇 开
        """
        if not self.device_map:
            yield event.plain_result("❌ 你的 device_map 配置为空或格式错误，请前往 WebUI 检查配置。")
            return

        # 检查设备是否在 WebUI 配置的字典中
        if device_name not in self.device_map:
            available_devices = "、".join(self.device_map.keys())
            yield event.plain_result(f"❌ 未找到设备 '{device_name}'。当前已配置的设备有：{available_devices}。")
            return

        if action not in ["开", "关"]:
            yield event.plain_result("❌ 操作指令有误，仅支持 '开' 或 '关'。")
            return

        did = self.device_map[device_name]
        is_on = True if action == "开" else False

        yield event.plain_result(f"⏳ 正在尝试将【{device_name}】设置为“{action}”...")

        try:
            service = await self._get_mi_service()
            
            # 构造 MIoT 属性设置请求 (默认标准开关参数 siid=2, piid=1)
            props = [{
                "did": did,
                "siid": 2,  
                "piid": 1,  
                "value": is_on
            }]
            
            result = await service.miot_set_props(props)
            
            if result and isinstance(result, list) and result[0].get('code') == 0:
                yield event.plain_result(f"✅ 成功！已将【{device_name}】状态设置为“{action}”。")
            else:
                yield event.plain_result(f"⚠️ 云端已接收指令，但设备执行异常。返回数据：{result}\n(可能是该设备的 siid/piid 参数不匹配)")
                
        except Exception as e:
            logger.error(f"控制米家设备时异常: {str(e)}", exc_info=True)
            yield event.plain_result(f"❌ 控制设备请求失败：{str(e)}")

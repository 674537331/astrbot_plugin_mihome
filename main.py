# -*- coding: utf-8 -*-
import asyncio
from micloud import MiCloud

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig

@register("astrbot_plugin_mihome_control", "RyanVaderAn", "米家设备云端控制插件 (第一阶段：设备发现)", "v1.0")
class MiHomeControlPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.username = self.config.get("mi_username", "")
        self.password = self.config.get("mi_password", "")
        
    def _login_and_get_devices_sync(self):
        """同步阻塞的登录与设备获取逻辑，供线程池调用"""
        if not self.username or not self.password:
            return None, "错误：请先在 AstrBot WebUI 面板中配置你的小米账号和密码。"
            
        try:
            logger.info("尝试使用配置的账号登录小米云端...")
            mc = MiCloud(self.username, self.password)
            
            # 第一步：必须先登录
            if not mc.login():
                return None, "登录失败：账号或密码错误，或者触发了风控。"
                
            logger.info("登录成功！正在拉取设备列表...")
            
            # 第二步：获取设备列表
            # micloud 默认请求 cn(中国大陆) 服务器，如果你在海外可能需要传参 mc.get_devices(country='sg')
            devices = mc.get_devices()
            
            if not devices:
                return None, "拉取成功，但你的账号下没有绑定任何支持 MIoT 协议的设备。"
                
            return devices, "成功获取设备列表！"
            
        except Exception as e:
            logger.error(f"小米云端交互异常: {str(e)}", exc_info=True)
            return None, f"获取设备时发生异常：{str(e)}"

    @filter.command("刷新米家")
    async def refresh_mihome_devices(self, event: AstrMessageEvent):
        """
        指令：/刷新米家
        用于测试账号连通性，并打印设备列表。
        """
        yield event.plain_result("正在连接小米云端，尝试拉取设备列表，请稍候...")
        
        # 将耗时的网络请求放入线程池，避免阻塞机器人其他消息
        loop = asyncio.get_running_loop()
        devices, msg = await loop.run_in_executor(None, self._login_and_get_devices_sync)
        
        if not devices:
            yield event.plain_result(msg)
            return
            
        # 整理设备信息，准备发送给用户
        result_texts = [f"✅ 成功找到 {len(devices)} 个设备：\n"]
        
        for idx, dev in enumerate(devices):
            name = dev.get('name', '未知设备')
            model = dev.get('model', '未知型号')
            did = dev.get('did', '未知DID')
            is_online = "在线" if dev.get('isOnline') else "离线"
            
            info = f"{idx + 1}. 【{name}】\n  - 型号: {model}\n  - DID: {did}\n  - 状态: {is_online}\n"
            result_texts.append(info)
            
        final_text = "\n".join(result_texts)
        # 截断以防消息过长，但通常一二十个设备问题不大
        if len(final_text) > 1000:
            final_text = final_text[:997] + "..."
            
        yield event.plain_result(final_text)

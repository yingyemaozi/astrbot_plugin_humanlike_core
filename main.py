from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import AstrBotConfig, logger
from astrbot.api.provider import ProviderRequest
from datetime import datetime, timezone, timedelta
import sqlite3
import math
import time
import os
import re
import asyncio
import random
import jieba

class HumanlikeCorePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config if config else {}
        logger.info("✨ 拟人化核心 V1.2 (终极生理与情感版) 已成功加载！")
        
        self.db_path = os.path.join(os.path.dirname(__file__), "humanlike_v1.db")
        self.init_db()
        
        self.decay_rate = self.config.get("decay_rate", 0.85)
        self.shadow_threshold = self.config.get("shadow_threshold", 30)
        self.max_len = self.config.get("max_reply_length", 20)
        self.enable_biology = self.config.get("enable_biology", True)

        self.synonyms = {
            "生日": ["蛋糕", "甜的", "许愿", "礼物", "过生"],
            "难过": ["烦", "emo", "伤心", "哭", "低落", "不开心"],
            "开心": ["高兴", "爽", "舒服", "笑", "愉快"],
            "讨厌": ["恶心", "受不了", "不喜欢", "烦人"]
        }
        self.soul_words = ["撑不住", "好累", "崩溃", "迷茫", "害怕", "孤独", "焦虑", "想哭", "其实我", "小时候", "秘密", "人生"]
        self.tacit_words = ["哈哈", "嘿", "嘻", "嘞", "啦", "呗", "嘛", "捏"]

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS memories
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id TEXT, content TEXT, score REAL,
                      last_time REAL, is_shadow INTEGER DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS stats
                     (user_id TEXT PRIMARY KEY,
                      mood REAL DEFAULT 50.0,
                      base_intimacy REAL DEFAULT 0.0,
                      tacit_intimacy REAL DEFAULT 0.0,
                      soul_intimacy REAL DEFAULT 0.0,
                      last_decay_day TEXT DEFAULT '',
                      base_added_today REAL DEFAULT 0.0,
                      tacit_added_today REAL DEFAULT 0.0,
                      soul_added_today REAL DEFAULT 0.0,
                      last_soul_time REAL DEFAULT 0.0,
                      energy REAL DEFAULT 85.0,
                      sleepiness REAL DEFAULT 0.0,
                      last_interact REAL DEFAULT 0.0,
                      is_sleeping INTEGER DEFAULT 0,
                      wake_up_time REAL DEFAULT 0.0,
                      sleep_quality REAL DEFAULT 1.0)''')
        conn.commit()
        conn.close()

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_message(self, event: AstrMessageEvent):
        text = event.message_str.strip()
        user_id = event.get_sender_id()
        if text.startswith('/'): return

        now = time.time()
        today_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        bj_hour = datetime.now(timezone(timedelta(hours=8))).hour
        
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute("SELECT mood, base_intimacy, tacit_intimacy, soul_intimacy, last_decay_day, base_added_today, tacit_added_today, soul_added_today, last_soul_time, energy, sleepiness, last_interact, is_sleeping, wake_up_time, sleep_quality FROM stats WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        
        if not row:
            c.execute("INSERT INTO stats (user_id, last_decay_day, last_interact) VALUES (?, ?, ?)", (user_id, today_str, now))
            mood, base, tacit, soul, last_day = 50.0, 0.0, 0.0, 0.0, today_str
            base_added, tacit_added, soul_added, last_soul_time = 0.0, 0.0, 0.0, 0.0
            energy, sleepiness, last_interact, is_sleeping, wake_up_time, sleep_quality = 85.0, 0.0, now, 0, 0.0, 1.0
        else:
            mood, base, tacit, soul, last_day = row[0], row[1], row[2], row[3], row[4]
            base_added = row[5] or 0.0
            tacit_added = row[6] or 0.0
            soul_added = row[7] or 0.0
            last_soul_time = row[8] or 0.0
            energy = row[9] if row[9] is not None else 85.0
            sleepiness = row[10] if row[10] is not None else 0.0
            last_interact = row[11] if row[11] else now
            is_sleeping = row[12] if row[12] is not None else 0
            wake_up_time = row[13] if row[13] else 0.0
            sleep_quality = row[14] if row[14] is not None else 1.0
            
            if last_day != today_str:
                mood = 50.0 + (mood - 50.0) * 0.25 + random.uniform(-5, 5)
                mood = max(0.0, min(100.0, mood))
                base_added, tacit_added, soul_added = 0.0, 0.0, 0.0
                c.execute("UPDATE stats SET mood = ?, last_decay_day = ?, base_added_today = 0.0, tacit_added_today = 0.0, soul_added_today = 0.0 WHERE user_id = ?", (mood, today_str, user_id))

        # ================= 1. 判定情感与互动 =================
        is_tacit = False
        is_soul = False
        
        if len(text) >= 3 and base < 50.0 and base_added < 4.0:
            base += 0.5
            base_added += 0.5
        
        if tacit < 25.0 and tacit_added < 2.0:
            if re.search(r'[（\(].*?[）\)]', text) or any(w in text for w in self.tacit_words):
                tacit += 0.5
                tacit_added += 0.5
                is_tacit = True

        if soul < 25.0 and soul_added < 1.5 and any(w in text for w in self.soul_words) and (now - last_soul_time > 86400):
            soul += 1.0
            soul_added += 1.0
            last_soul_time = now
            is_soul = True

        if "开心" in text: mood = min(100.0, mood + 5.0)
        if "难过" in text: mood = max(0.0, mood - 5.0)

        # ================= 2. 生理与心理引擎 =================
        if self.enable_biology:
            if is_sleeping == 1:
                if now >= wake_up_time:
                    is_sleeping = 0
                    energy = 85.0 if sleep_quality >= 0.8 else 65.0
                    sleepiness = 0.0
                else:
                    # 被吵醒判定
                    if now - last_interact < 300: 
                        if any(w in text for w in ["对不起", "吵醒", "快睡", "晚安", "乖", "抱抱"]):
                            mood = min(100.0, mood + 2.0)
                            logger.info("🥺 被温柔唤醒，心情微涨")
                        elif any(w in text for w in ["陪我", "一会", "嘛", "好不好"]):
                            mood = max(0.0, mood - 2.0)
                            logger.info("😪 强行撒娇，无奈陪伴")
                        elif any(w in text for w in ["起来", "别睡", "嗨", "醒醒"]):
                            mood = max(0.0, mood - 15.0)
                            logger.info("😡 强行吵醒，起床气爆发！")
                            
                        is_sleeping = 0
                        energy = 10.0
            else:
                delta_hours = (now - last_interact) / 3600.0
                
                # 心情自然平复机制
                if delta_hours > 0.5:
                    if mood > 50.0:
                        mood = max(50.0, mood - (delta_hours * 3.0))
                    elif mood < 50.0:
                        mood = min(50.0, mood + (delta_hours * 3.0))

                # 体力自然流逝与回血
                if delta_hours > 2.0:
                    energy = min(100.0, energy + 10.0)
                    sleepiness += delta_hours * 2.0
                else:
                    if 7 <= bj_hour < 18: drain = 2.0
                    elif 18 <= bj_hour < 23: drain = 6.0
                    else: drain = 10.0
                    energy -= (delta_hours * drain) + 0.5 
                    
                    # 睡眠值累积
                    sleep_growth_rate = 0.0
                    if 8 <= bj_hour < 18: sleep_growth_rate = 3.0
                    elif 18 <= bj_hour < 23: sleep_growth_rate = 8.0
                    elif 23 <= bj_hour or bj_hour < 2: sleep_growth_rate = 15.0
                    else: sleep_growth_rate = 25.0
                    
                    sleepiness += (delta_hours * sleep_growth_rate) + 0.1
                    
                    # 多巴胺抵消
                    if is_tacit: sleepiness -= 3.0
                    if is_soul: sleepiness -= 5.0
                    if energy < 20.0: sleepiness += 2.0

                # 睡眠时间锚点 (增强版)
                if 23 <= bj_hour < 24: 
                    sleepiness = max(sleepiness, 70.0) # 11点微困
                elif 0 <= bj_hour < 2: 
                    sleepiness = max(sleepiness, 85.0) # 过了12点，强行极度困倦
                elif 2 <= bj_hour < 6: 
                    sleepiness = max(sleepiness, 95.0) # 凌晨2点后，神仙也得睡
                
                # 体力时间锚点
                if 23 <= bj_hour or bj_hour < 6: energy = min(energy, 60.0) 
                elif 18 <= bj_hour < 23: energy = min(energy, 75.0)
                
                # 触发强制睡眠
                if energy <= 5.0 or sleepiness >= 100.0:
                    is_sleeping = 1
                    wake_up_time = now + random.uniform(6*3600, 9*3600)
                    energy = max(0.0, energy)

        # ================= 3. 记忆逻辑 =================
        score = 0
        if len(text) >= 3 and not text.endswith(('?', '？', '吗', '呢')):
            if any(w in text for w in ["记错了", "不对", "记住", "忘了"]): score = 100.0
            elif any(w in text for w in ["难过", "崩溃", "开心", "激动", "喜欢", "讨厌"]): score = 70.0
            elif "我" in text or "我的" in text: score = 60.0
            else: score = 50.0
        if score > 0:
            c.execute("INSERT INTO memories (user_id, content, score, last_time) VALUES (?, ?, ?, ?)", (user_id, text, score, now))

        # ================= 4. 存库收尾 =================
        c.execute("UPDATE stats SET mood=?, base_intimacy=?, tacit_intimacy=?, soul_intimacy=?, base_added_today=?, tacit_added_today=?, soul_added_today=?, last_soul_time=?, energy=?, sleepiness=?, last_interact=?, is_sleeping=?, wake_up_time=?, sleep_quality=? WHERE user_id=?",
                  (mood, base, tacit, soul, base_added, tacit_added, soul_added, last_soul_time, energy, sleepiness, now, is_sleeping, wake_up_time, sleep_quality, user_id))
        conn.commit()
        conn.close()

    @filter.on_llm_request()
    async def inject_context(self, event: AstrMessageEvent, req: ProviderRequest):
        bj_time = datetime.now(timezone(timedelta(hours=8)))
        hour = bj_time.hour
        time_status = "日常时间" if 7 <= hour < 23 else "深夜"
        user_id = event.get_sender_id()

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT mood, energy, sleepiness, is_sleeping FROM stats WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        mood, energy, sleepiness, is_sleeping = row if row else (50.0, 85.0, 0.0, 0)

        c.execute("SELECT content, score, last_time, is_shadow FROM memories WHERE user_id = ?", (user_id,))
        rows = c.fetchall()
        valid_memories = []
        for content, score, last_time, is_shadow in rows:
            if is_shadow == 0: valid_memories.append((score, content))
            elif any(word in content for word in self.synonyms.keys()): valid_memories.append((score, f"（模糊的记忆）你隐约记得：{content}..."))
        conn.close()

        top_memories = [m[1] for m in valid_memories[:3]]
        memory_text = "你记得：" + "，".join(top_memories) if top_memories else "你暂时没有清晰的记忆。"

        if is_sleeping == 1: 
            state_text = "你现在被吵醒了，带着起床气，极度困倦。"
        elif energy < 30 or sleepiness > 90: 
            state_text = "你现在极度困倦，半梦半醒，说话断断续续，想打哈欠。"
        elif mood < 30: 
            state_text = "你现在心情极度低落。"
        else: 
            state_text = "你现在精神正常。"

        sys_prompt = f"""
【你的状态】{time_status}。{state_text} 当前体力:{energy:.0f}，睡眠值:{sleepiness:.0f}。
【关于对方的记忆】{memory_text}
【聊天规则】
1. 像微信聊天，控制在{self.max_len}个字以内。
2. 根据你的体力、睡眠和心情状态，表现出对应的语气。如果困了就直说，不要硬撑。
"""
        if req.system_prompt: req.system_prompt += "\n" + sys_prompt
        else: req.system_prompt = sys_prompt

    @filter.on_decorating_result()
    async def process_response(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result: return
        result_text = result.get_plain_text()
        if not result_text: return
        parts = [p.strip() for p in re.split(r'(?<=[。！？!?])', result_text) if p.strip()]
        if len(parts) <= 1: event.set_result(event.plain_result(result_text)); return
        for i, part in enumerate(parts[:3]):
            if i == 0: yield event.plain_result(part)
            else:
                await asyncio.sleep(1.0)
                yield event.plain_result(part)

    @filter.command("status")
    async def status(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        now = time.time()
        bj_hour = datetime.now(timezone(timedelta(hours=8))).hour
        
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT mood, base_intimacy, tacit_intimacy, soul_intimacy, energy, sleepiness, is_sleeping, base_added_today, tacit_added_today, soul_added_today FROM stats WHERE user_id=?", (user_id,))
        row = c.fetchone()
        
        if row:
            mood, base, tacit, soul, energy, sleep, is_sleep, b_added, t_added, s_added = row
            
            # 临时修正：如果是查面板，也强行展示它该有的困意
            if is_sleep == 0:
                if 0 <= bj_hour < 2: sleep = max(sleep, 85.0)
                elif 23 <= bj_hour < 24: sleep = max(sleep, 70.0)
                elif 2 <= bj_hour < 6: sleep = max(sleep, 95.0)

            conn.close()
            sleep_str = "💤 正在睡觉" if is_sleep else "👀 醒着"
            b_added = b_added or 0.0
            t_added = t_added or 0.0
            s_added = s_added or 0.0
            yield event.plain_result(
                f"❤️ 心情: {mood:.1f}/100\n"
                f"🔋 体力: {energy:.1f}/100\n"
                f"😴 睡眠值: {sleep:.1f}/100\n"
                f"状态: {sleep_str}\n"
                f"🤝 基础好感: {base:.1f}/50 (今日加: {b_added:.1f}/4.0)\n"
                f"🔥 默契羁绊: {tacit:.1f}/25 (今日加: {t_added:.1f}/2.0)\n"
                f"✨ 灵魂共鸣: {soul:.1f}/25 (今日加: {s_added:.1f}/1.5)"
            )

    @filter.command("ping")
    async def ping(self, event: AstrMessageEvent):
        yield event.plain_result("V1.2 终极生理与情感版运行正常。")

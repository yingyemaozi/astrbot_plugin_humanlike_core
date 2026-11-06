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
        logger.info("✨ 拟人化核心 V1.2 (生理引擎版) 已成功加载！")
        
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
        # 新增了体力、睡眠、时间戳等生物钟字段
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

        # ================= 1. 获取或初始化数据 =================
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

        # ================= 2. 生理引擎计算 (体力与睡眠) =================
        if self.enable_biology:
            # 2.1 睡眠唤醒判定
            if is_sleeping == 1:
                if now >= wake_up_time:
                    is_sleeping = 0
                    # 优质睡眠回85，劣质回65
                    energy = 85.0 if sleep_quality >= 0.8 else 65.0
                    sleepiness = 0.0
                    logger.info(f"🌅 自然醒来，体力恢复至 {energy}")
                else:
                    # 被吵醒判定
                    if now - last_interact < 300: # 连发消息
                        if any(w in text for w in ["对不起", "吵醒", "快睡", "晚安"]):
                            energy -= 1.0 # 懂事安慰，轻微耗电
                            sleep_quality -= 0.1 # 睡眠质量受损
                        else:
                            mood = max(0.0, mood - 15.0) # 起床气爆发
                            sleep_quality = 0.5 # 劣质睡眠
                            logger.info("😡 触发起床气！")
            else:
                # 2.2 计算时间差
                delta_hours = (now - last_interact) / 3600.0
                
                # 2.3 自然流逝与回血
                if delta_hours > 2.0:
                    energy = min(100.0, energy + 10.0) # 超过2小时发呆回血
                
                # 基于时间的自然流逝 (简化版，按平均值)
                drain_rate = 2.0 if 8 <= bj_hour < 18 else (6.0 if 18 <= bj_hour < 23 else 10.0)
                energy -= delta_hours * drain_rate
                sleepiness += delta_hours * (drain_rate * 1.5)

                # 2.4 聊天交互消耗 (按时间差算，白天每小时5点，晚上翻倍)
                if delta_hours < 1.0: # 连续高频聊天
                    chat_cost = (delta_hours / 1.0) * (5.0 if bj_hour < 18 else (8.0 if bj_hour < 23 else 12.0))
                    energy -= chat_cost
                else:
                    energy -= 2.0 # 偶尔发一句，固定扣点
                
                # 三餐回血 (简化的定点判定)
                if 7 <= bj_hour < 9 and random.random() < 0.1: energy = min(100.0, energy + 5.0)
                if 12 <= bj_hour < 13 and random.random() < 0.1: energy = min(100.0, energy + 8.0)
                if 18 <= bj_hour < 19 and random.random() < 0.1: energy = min(100.0, energy + 8.0)

                # 2.5 触发睡眠
                if energy <= 5.0 or sleepiness >= 100.0:
                    is_sleeping = 1
                    wake_up_time = now + random.uniform(6*3600, 9*3600)
                    energy = max(0.0, energy)
                    logger.info(f"💤 困得不行了，开始睡觉，预计 {int((wake_up_time-now)/3600)} 小时后醒。")

        # ================= 3. 记忆与三阶情感计算 =================
        # (这里保留你之前 V1.1 的逻辑：打分、入库、默契、共鸣、心情变动)
        score = 0
        if len(text) >= 3 and not text.endswith(('?', '？', '吗', '呢')):
            if any(w in text for w in ["记错了", "不对", "记住", "忘了"]):
                score = 100.0
                c.execute("UPDATE memories SET is_shadow = 1 WHERE user_id = ? AND content LIKE ?", (user_id, f"%{text[:5]}%"))
            elif any(w in text for w in ["难过", "崩溃", "开心", "激动", "喜欢", "讨厌"]): score = 70.0
            elif "我" in text or "我的" in text: score = 60.0
            else: score = 50.0
        if score > 0:
            c.execute("INSERT INTO memories (user_id, content, score, last_time) VALUES (?, ?, ?, ?)", (user_id, text, score, now))

        if len(text) >= 3 and base < 50.0 and base_added < 4.0:
            base += 0.5
            base_added += 0.5
        
        if tacit < 25.0 and tacit_added < 2.0:
            if re.search(r'[（\(].*?[）\)]', text) or any(w in text for w in self.tacit_words) or text.endswith(('嘛', '呢', '呀')):
                tacit += 0.5; tacit_added += 0.5
            elif any(w in text for w in ["对不起", "原谅", "我错"]):
                tacit += 1.0; tacit_added += 1.0

        if soul < 25.0 and soul_added < 1.5 and any(w in text for w in self.soul_words) and (now - last_soul_time > 86400):
            soul += 1.0; soul_added += 1.0; last_soul_time = now

        if "开心" in text: mood = min(100.0, mood + 5.0)
        if "难过" in text: mood = max(0.0, mood - 5.0)

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
        user_text = event.message_str
        now = time.time()

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT mood, base_intimacy, tacit_intimacy, soul_intimacy, energy, sleepiness, is_sleeping FROM stats WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        mood, base, tacit, soul, energy, sleepiness, is_sleeping = row if row else (50.0, 0.0, 0.0, 0.0, 85.0, 0.0, 0)
        total_intimacy = base + tacit + soul

        # 记忆召回
        c.execute("SELECT id, content, score, last_time, is_shadow FROM memories WHERE user_id = ?", (user_id,))
        rows = c.fetchall()
        expand_keywords = [k for k, v in self.synonyms.items() if any(rw in user_text for rw in v)]
        valid_memories = []
        for mid, content, score, last_time, is_shadow in rows:
            days = (now - last_time) / 86400.0
            current_score = score * math.pow(self.decay_rate, days)
            if current_score < self.shadow_threshold and is_shadow == 0:
                c.execute("UPDATE memories SET is_shadow = 1 WHERE id = ?", (mid,))
                is_shadow = 1
            if is_shadow == 0: valid_memories.append((current_score, content))
            elif any(word in content for word in expand_keywords):
                valid_memories.append((current_score, f"（模糊的记忆）你隐约记得：{content}..."))
        conn.commit()
        conn.close()

        valid_memories.sort(reverse=True, key=lambda x: x[0])
        top_memories = [m[1] for m in valid_memories[:3]]
        memory_text = "你记得：" + "，".join(top_memories) if top_memories else "你暂时没有清晰的记忆。"

        # 关系与状态判定
        if total_intimacy > 80 and soul > 15: relation = "你们是灵魂伴侣/情侣。"
        elif total_intimacy > 60 and tacit > 10: relation = "你们是默契的死党/挚友。"
        elif total_intimacy > 30: relation = "你们是要好的朋友。"
        else: relation = "你们刚认识，保持礼貌。"

        if is_sleeping == 1: 
            state_text = "你现在被吵醒了，极度困倦，带着起床气，只想马上继续睡。"
        elif energy < 30:
            state_text = "你现在体力极低，极其慵懒，完全不想动，说话非常短。"
        elif sleepiness > 90:
            state_text = "你现在处于半梦半醒状态，说话断断续续，想打哈欠，甚至可以打错字。"
        elif mood < 30:
            state_text = "你现在心情极度低落，不想说话。"
        elif mood > 70:
            state_text = "你现在心情极好，话多，喜欢用波浪号。"
        else:
            state_text = "你现在精神正常，心情平静。"

        sys_prompt = f"""
【你的状态】{time_status}。{state_text} 当前体力:{energy:.0f}，睡眠值:{sleepiness:.0f}。
【你的关系】{relation}
【关于对方的记忆】{memory_text}
【聊天规则（绝对遵守）】
1. 像微信聊天，禁止客服腔。
2. 控制在{self.max_len}个字以内。
3. 记忆里的事是对方发生的，不是你的！
4. 根据你的体力、睡眠和心情状态，表现出对应的语气。
"""
        if req.system_prompt: req.system_prompt += "\n" + sys_prompt
        else: req.system_prompt = sys_prompt

    @filter.on_decorating_result()
    async def process_response(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result: return
        result_text = result.get_plain_text()
        if not result_text: return
        for word in ["作为一个AI", "人工智能", "很高兴为您服务"]: result_text = result_text.replace(word, "")
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
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT mood, base_intimacy, tacit_intimacy, soul_intimacy, energy, sleepiness, is_sleeping FROM stats WHERE user_id=?", (user_id,))
        row = c.fetchone()
        conn.close()
        if row:
            mood, base, tacit, soul, energy, sleep, is_sleep = row
            sleep_str = "💤 正在睡觉" if is_sleep else "👀 醒着"
            yield event.plain_result(f"❤️ 心情: {mood:.1f}/100\n🔋 体力: {energy:.1f}/100\n😴 睡眠值: {sleep:.1f}/100\n状态: {sleep_str}\n🤝 基础好感: {base:.1f}/50\n🔥 默契羁绊: {tacit:.1f}/25\n✨ 灵魂共鸣: {soul:.1f}/25")

    @filter.command("forget")
    async def forget(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DELETE FROM memories WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM stats WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        yield event.plain_result("（委屈巴巴）好啦，我已经把关于你的事情全都忘掉了...")

    @filter.command("ping")
    async def ping(self, event: AstrMessageEvent):
        yield event.plain_result("V1.2 生理引擎版运行正常。")

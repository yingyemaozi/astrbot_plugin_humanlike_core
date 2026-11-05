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
        logger.info("✨ 拟人化核心 V1.0 已加载！")
        
        self.db_path = os.path.join(os.path.dirname(__file__), "humanlike_v1.db")
        self.init_db()
        
        self.decay_rate = self.config.get("decay_rate", 0.85)
        self.shadow_threshold = self.config.get("shadow_threshold", 30)
        self.max_len = self.config.get("max_reply_length", 20)
        self.enable_diary = self.config.get("enable_diary", True)

        # 语义联想库
        self.synonyms = {
            "生日": ["蛋糕", "甜的", "许愿", "礼物", "过生"],
            "难过": ["烦", "emo", "伤心", "哭", "低落", "不开心"],
            "开心": ["高兴", "爽", "舒服", "笑", "愉快"],
            "讨厌": ["恶心", "受不了", "不喜欢", "烦人"]
        }

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        # 记忆表
        c.execute('''CREATE TABLE IF NOT EXISTS memories
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id TEXT, content TEXT, score REAL,
                      last_time REAL, is_shadow INTEGER DEFAULT 0)''')
        # 情感与三阶关系表
        c.execute('''CREATE TABLE IF NOT EXISTS stats
                     (user_id TEXT PRIMARY KEY,
                      mood REAL DEFAULT 50.0,
                      base_intimacy REAL DEFAULT 0.0,
                      tacit_intimacy REAL DEFAULT 0.0,
                      soul_intimacy REAL DEFAULT 0.0,
                      last_decay_day TEXT DEFAULT '')''')
        # 日记表
        c.execute('''CREATE TABLE IF NOT EXISTS diaries
                     (user_id TEXT, date_str TEXT, content TEXT,
                      PRIMARY KEY (user_id, date_str))''')
        conn.commit()
        conn.close()

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_message(self, event: AstrMessageEvent):
        text = event.message_str.strip()
        user_id = event.get_sender_id()
        
        if text.startswith('/'): return

        now = time.time()
        today_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # ================= 1. 记忆漏斗过滤与打分 =================
        score = 0
        if len(text) >= 3 and not text.endswith(('?', '？', '吗', '呢')):
            if any(w in text for w in ["记错了", "不对", "记住", "忘了"]):
                score = 100.0
                # 纠错机制：作废旧相关记忆
                c.execute("UPDATE memories SET is_shadow = 1 WHERE user_id = ? AND content LIKE ?", (user_id, f"%{text[:5]}%"))
            elif any(w in text for w in ["难过", "崩溃", "开心", "激动", "喜欢", "讨厌"]):
                score = 70.0
            elif "我" in text or "我的" in text:
                score = 60.0
            else:
                score = 50.0

        if score > 0:
            c.execute("INSERT INTO memories (user_id, content, score, last_time) VALUES (?, ?, ?, ?)",
                      (user_id, text, score, now))

        # ================= 2. 每日情感回归与数据初始化 =================
        c.execute("SELECT mood, base_intimacy, tacit_intimacy, soul_intimacy, last_decay_day FROM stats WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        
        if not row:
            c.execute("INSERT INTO stats (user_id, last_decay_day) VALUES (?, ?)", (user_id, today_str))
            mood, base, tacit, soul = 50.0, 0.0, 0.0, 0.0
        else:
            mood, base, tacit, soul, last_day = row
            # 心情回归函数 (跨天触发)
            if last_day != today_str:
                mood = 50.0 + (mood - 50.0) * 0.25 + random.uniform(-5, 5)
                mood = max(0.0, min(100.0, mood))
                c.execute("UPDATE stats SET mood = ?, last_decay_day = ? WHERE user_id = ?", (mood, today_str, user_id))

        # ================= 3. 三阶亲密度增长机制 =================
        # 基础增长 (上限50)
        base = min(50.0, base + 0.5)
        
        # 默契/羁绊增长 (吵架和解、吐槽)
        if any(w in text for w in ["对不起", "原谅", "吵架", "傻逼", "卧槽"]):
            tacit = min(25.0, tacit + 1.5)
            
        # 灵魂共鸣增长 (深夜交心、秘密)
        bj_hour = datetime.now(timezone(timedelta(hours=8))).hour
        if (bj_hour >= 23 or bj_hour < 4) and any(w in text for w in ["秘密", "难过", "倾诉", "其实我"]):
            soul = min(25.0, soul + 2.0)

        # 心情值微调
        if "开心" in text: mood = min(100.0, mood + 5.0)
        if "难过" in text: mood = max(0.0, mood - 5.0)

        c.execute("UPDATE stats SET mood=?, base_intimacy=?, tacit_intimacy=?, soul_intimacy=? WHERE user_id=?",
                  (mood, base, tacit, soul, user_id))
        conn.commit()
        conn.close()

        # ================= 4. 赛博日记本触发 (每日一次) =================
        if self.enable_diary:
            await self.generate_diary(user_id, today_str)

    async def generate_diary(self, user_id, today_str):
        # 检查今天有没有写日记
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT content FROM diaries WHERE user_id=? AND date_str=?", (user_id, today_str))
        if c.fetchone():
            conn.close()
            return
            
        # 提取今天的记忆
        c.execute("SELECT content, score FROM memories WHERE user_id=? AND last_time > ?", (user_id, time.time() - 86400))
        today_memories = c.fetchall()
        conn.close()

        if not today_memories:
            return

        # 随机决定字数与心情
        mem_str = "，".join([m[0] for m in today_memories[:5]])
        diary_content = f"（基于以下记忆，用第一人称写一篇简短的日记，字数随意，符合当前心境）：{mem_str}"
        
        # 这里为了不阻塞主线程，简单起个任务(实际开发建议用 context 提供的 llm 接口)
        # 为了 V1.0 稳定，我们先用一个占位符，晚上测试可以直接在终端看日志
        logger.info(f"📝 正在生成 {user_id} 的日记，今日素材: {mem_str}")

    @filter.on_llm_request()
    async def inject_context(self, event: AstrMessageEvent, req: ProviderRequest):
        bj_time = datetime.now(timezone(timedelta(hours=8)))
        hour = bj_time.hour
        time_status = "日常时间" if 7 <= hour < 23 else "深夜，你在熬夜陪对方"

        user_id = event.get_sender_id()
        user_text = event.message_str
        now = time.time()

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # 获取情感与关系
        c.execute("SELECT mood, base_intimacy, tacit_intimacy, soul_intimacy FROM stats WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        mood, base, tacit, soul = row if row else (50, 0, 0, 0)
        total_intimacy = base + tacit + soul

        # 1. 记忆检索与衰减计算
        c.execute("SELECT id, content, score, last_time, is_shadow FROM memories WHERE user_id = ?", (user_id,))
        rows = c.fetchall()

        expand_keywords = []
        for core_word, related_words in self.synonyms.items():
            if any(rw in user_text for rw in related_words):
                expand_keywords.append(core_word)

        valid_memories = []
        
        for mid, content, score, last_time, is_shadow in rows:
            days = (now - last_time) / 86400.0
            # 记忆衰减函数 (每天乘以 0.85)
            current_score = score * math.pow(self.decay_rate, days)
            
            # 如果分数跌破阈值，且原来不是影子，更新数据库
            if current_score < self.shadow_threshold and is_shadow == 0:
                c.execute("UPDATE memories SET is_shadow = 1 WHERE id = ?", (mid,))
                is_shadow = 1

            # 常规检索，跳过影子
            if is_shadow == 0:
                valid_memories.append((current_score, content))
            else:
                # 影子唤醒机制 (只有触发联想词才模糊唤醒)
                if any(word in content for word in expand_keywords):
                    valid_memories.append((current_score, f"（模糊的记忆）你隐约记得：{content}..."))
                    logger.info(f"🌑 唤醒模糊影子记忆: {content}")

        conn.commit()
        conn.close()

        # 排序取前3
        valid_memories.sort(reverse=True, key=lambda x: x[0])
        top_memories = [m[1] for m in valid_memories[:3]]

        # 2. 关系判定
        if total_intimacy > 80 and soul > 15:
            relation = "你们是灵魂伴侣/情侣，你极度依赖对方，可以撒娇吃醋。"
        elif total_intimacy > 60 and tacit > 10:
            relation = "你们是默契的死党/挚友，可以互相损对方。"
        elif total_intimacy > 30:
            relation = "你们是要好的朋友，聊天自然。"
        else:
            relation = "你们刚认识，保持礼貌。"

        # 心情表现
        if mood < 30:
            mood_text = "你现在心情极度低落，不想说话，回复极短或带省略号。"
        elif mood > 70:
            mood_text = "你现在心情极好，话多，喜欢用波浪号。"
        else:
            mood_text = "你现在心情平静。"

        memory_text = "你记得：" + "，".join(top_memories) if top_memories else "你暂时没有清晰的记忆。"

        # 3. 注入 Prompt
        sys_prompt = f"""
【你的状态】{time_status}。{mood_text}
【你的关系】{relation}
【关于对方的记忆】{memory_text}
【聊天规则（绝对遵守）】
1. 像微信聊天，禁止客服腔。
2. 控制在{self.max_len}个字以内。
3. 如果想说两句话，请用句号或问号隔开。
4. 记忆里的事是对方发生的，不是你的！
5. 说话带点口语词（呀、啦、我觉得），别像百科全书。
"""
        if req.system_prompt:
            req.system_prompt += "\n" + sys_prompt
        else:
            req.system_prompt = sys_prompt

    @filter.on_decorating_result()
    async def process_response(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result: return
        result_text = result.get_plain_text()
        if not result_text: return

        # 清洗机器味
        for word in ["作为一个AI", "人工智能", "很高兴为您服务"]:
            result_text = result_text.replace(word, "")

        # 按标点切分，最多3段
        parts = [p.strip() for p in re.split(r'(?<=[。！？!?])', result_text) if p.strip()]
        parts = parts[:3]

        if len(parts) <= 1:
            event.set_result(event.plain_result(result_text))
            return

        for i, part in enumerate(parts):
            if i == 0:
                yield event.plain_result(part)
            else:
                await asyncio.sleep(1.0)
                yield event.plain_result(part)

    @filter.command("status")
    async def status(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT mood, base_intimacy, tacit_intimacy, soul_intimacy FROM stats WHERE user_id=?", (user_id,))
        row = c.fetchone()
        conn.close()
        if row:
            mood, base, tacit, soul = row
            yield event.plain_result(f"❤️ 心情: {mood:.1f}/100\n🤝 基础好感: {base:.1f}/50\n🔥 默契羁绊: {tacit:.1f}/25\n✨ 灵魂共鸣: {soul:.1f}/25")

    @filter.command("ping")
    async def ping(self, event: AstrMessageEvent):
        yield event.plain_result("我在呢，V1.0 完全体运行正常。")
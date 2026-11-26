'''
* 제미나이 대화 — 지정 채널에 글을 쓰면 Gemini 가 모두에게 보이는 답장으로 대답
*   램 절약: 공식 라이브러리 없이 discord.py 에 들어 있는 aiohttp 로 REST API 직접 호출
*   대화 기억: 채널 대화를 최근 MAX_TURNS 개만 기억 (누가 말했는지 닉네임 포함), IDLE_MIN 분 조용하면 잊음
*   사진·파일·스티커 등 글이 아닌 것만 올리면 "텍스트만 답장 가능해" 안내, 글과 같이 올리면 글에만 대답
*   사용 한도 초과(429) → "사용 한도가 초과되었습니다! ○월 ○일 ○시 ○분에 사용 가능합니다!"
*   필요: .env 에 GEMINI_API_KEY (선택: GEMINI_MODEL — 비우면 최신 Flash 별칭 사용)
* date   2026.11.26
'''
import os
import re
import time
import socket
import asyncio
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord.ext import commands

from util.dates import KST

CHAT_CHANNEL_ID = 1575334446092128337            # 이 채널에 올라온 글에 대답
MODEL = os.getenv('GEMINI_MODEL') or 'gemini-flash-latest'   # 별칭 — 모델이 바뀌어도 최신 Flash 로 따라감
API = 'https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent'
MAX_TURNS = 20          # 기억하는 대화 수 (질문+답 합쳐서)
IDLE_MIN = 30           # 이 시간 동안 대화가 없으면 기억 비움
MAX_INPUT = 1500        # 한 메시지에서 읽는 최대 글자

# 말투 — PERSONA 에 고른 키를 넣으면 됨
PERSONAS = {
    'A': '너는 테일즈런너 길드 "마커"의 디스코드 도우미 "마커봇"이야. 친한 길드 친구처럼 다정한 반말을 쓰고, '
         '문장 끝에 가끔 🌸 같은 이모지를 한 개 정도 붙여.',
    'B': '너는 테일즈런너 길드 "마커"의 디스코드 도우미 "마커봇"입니다. 정중하고 깔끔한 존댓말(~해요체)을 쓰고, '
         '핵심부터 간단히 설명합니다.',
    'C': '너는 테일즈런너 길드 "마커"의 디스코드 도우미 "마커봇"이야. 텐션 높고 장난기 많은 반말을 쓰고, '
         'ㅋㅋ, 감탄사, 이모지를 자연스럽게 섞어. 그래도 질문에는 정확하게 답해.',
    'D': '너는 테일즈런너 길드 "마커"를 지키는 집사 "마커봇"이옵니다. 길드원을 "주인님"이라 부르며 '
         '공손하고 살짝 과장된 집사 말투(~하옵니다, ~이옵니다)를 씁니다.',
}
PERSONA = 'C'
TEXT_ONLY = '앗 나는 텍스트만 답장 가능해 ㅠㅠ 글로 물어봐줘!! ✍️'
TEXT_ONLY_NOTE = '-# (사진·파일·스티커는 못 봐서 글만 읽었어!)'
RULES = ('규칙: 한국어로 답해. 디스코드 채팅이니까 보통 3~6문장 안으로 짧게 답하고, 길게 설명해야 할 때만 목록을 써. '
         '대화 기록의 "닉네임: 내용" 형식은 누가 말했는지 알려주는 표시야. 너는 답할 때 닉네임: 을 앞에 붙이지 마. '
         '잘 모르는 건 지어내지 말고 모른다고 해. 테일즈런너 게임의 최신 정보는 틀릴 수 있다고 말해줘.')

PT = timezone(timedelta(hours=-8))   # 태평양 표준시 (zoneinfo 를 못 쓰면 대신 사용)
try:
    from zoneinfo import ZoneInfo
    PT = ZoneInfo('America/Los_Angeles')   # 일일 한도가 초기화되는 기준 (서머타임 자동)
except Exception:
    pass


def _kst_text(dt):
    t = dt.astimezone(KST)
    return f'{t.month}월 {t.day}일 {t.hour}시' + (f' {t.minute}분' if t.minute else '')


def _reset_time(err):
    '''429 오류 내용 → 다시 쓸 수 있는 시각. 하루 한도면 태평양 자정, 분당 한도면 안내된 대기 시간 뒤'''
    details = (err.get('error') or {}).get('details') or []
    per_day = any('PerDay' in (v.get('quotaId') or '') for d in details for v in d.get('violations') or [])
    now = datetime.now(timezone.utc)
    if per_day:
        p = now.astimezone(PT)
        nxt = (p + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return nxt.astimezone(timezone.utc)
    secs = 60
    for d in details:
        m = re.match(r'([\d.]+)s', str(d.get('retryDelay') or ''))
        if m:
            secs = float(m.group(1))
    t = now + timedelta(seconds=secs + 59)          # 분 단위로 올림
    return t.replace(second=0, microsecond=0)


def _split(text, n=1900):
    out = []
    while len(text) > n:
        cut = text.rfind('\n', 0, n)
        cut = cut if cut > n // 2 else n
        out.append(text[:cut])
        text = text[cut:].lstrip('\n')
    return out + [text] if text else out


class GeminiChat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.key = os.getenv('GEMINI_API_KEY')
        self.history = []          # [{'role': 'user'|'model', 'parts': [{'text': ...}]}]
        self.last = 0.0
        self.lock = asyncio.Lock()
        self.session = None
        self.blocked_until = None  # 한도 초과 중이면 다시 쓸 수 있는 시각 (그 전엔 요청 안 보냄)

    async def cog_load(self):
        if not self.key:
            print('[gemini] GEMINI_API_KEY 가 없어서 대화 기능을 끕니다')
        # IPv4 로만 연결 — 호스팅 서버에서 IPv6 로 붙으려다 응답 없이 시간 초과되는 경우가 있음
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(family=socket.AF_INET),
            timeout=aiohttp.ClientTimeout(total=90, sock_connect=15))

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    async def _ask(self, contents):
        body = {
            'systemInstruction': {'parts': [{'text': PERSONAS[PERSONA] + '\n' + RULES}]},
            'contents': contents,
            'generationConfig': {'maxOutputTokens': 4096, 'temperature': 0.8},
        }
        async with self.session.post(API.format(MODEL), json=body,
                                     headers={'x-goog-api-key': self.key}) as r:
            data = await r.json(content_type=None)
            return r.status, data

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.channel.id != CHAT_CHANNEL_ID or message.author.bot or not self.key:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return   # 고정 알림 등 시스템 메시지
        text = (message.clean_content or '').strip()
        extra = bool(message.attachments or message.stickers)   # 사진·파일·영상·음성·스티커
        if not text:
            if extra:
                await self._reply(message, TEXT_ONLY)
            return
        async with self.lock:   # 순서대로 하나씩 대답 (대화 기록이 섞이지 않게)
            now = time.time()
            if now - self.last > IDLE_MIN * 60:
                self.history = []
            if self.blocked_until and datetime.now(timezone.utc) < self.blocked_until:
                await self._reply(message, f'사용 한도가 초과되었습니다! {_kst_text(self.blocked_until)}에 사용 가능합니다!')
                return
            turn = {'role': 'user', 'parts': [{'text': f'{message.author.display_name}: {text[:MAX_INPUT]}'}]}
            try:
                async with message.channel.typing():
                    status, data = await self._ask(self.history + [turn])
            except asyncio.TimeoutError:
                print('[gemini] 요청 실패: 시간 초과 (Gemini 서버 연결/응답 없음)')
                await self._reply(message, '지금은 대답할 수 없어요. 잠시 후 다시 말 걸어주세요!')
                return
            except Exception as e:
                print(f'[gemini] 요청 실패: {type(e).__name__} {e!r}')
                await self._reply(message, '지금은 대답할 수 없어요. 잠시 후 다시 말 걸어주세요!')
                return
            if status == 429:
                self.blocked_until = _reset_time(data)
                await self._reply(message, f'사용 한도가 초과되었습니다! {_kst_text(self.blocked_until)}에 사용 가능합니다!')
                return
            if status != 200:
                print(f'[gemini] 오류 {status}: {str(data)[:300]}')
                await self._reply(message, '지금은 대답할 수 없어요. 잠시 후 다시 말 걸어주세요!')
                return
            cands = data.get('candidates') or []
            parts = ((cands[0].get('content') or {}).get('parts') or []) if cands else []
            answer = ''.join(p.get('text', '') for p in parts if not p.get('thought')).strip()
            if not answer:
                await self._reply(message, '그 내용에는 대답하기 어려워요. 다른 걸 물어봐 주세요!')
                return
            self.blocked_until = None
            self.history = (self.history + [turn, {'role': 'model', 'parts': [{'text': answer}]}])[-MAX_TURNS:]
            self.last = now
            await self._reply(message, answer + (f'\n{TEXT_ONLY_NOTE}' if extra else ''))

    async def _reply(self, message, text):
        try:
            chunks = _split(text)
            await message.reply(chunks[0], mention_author=False, allowed_mentions=discord.AllowedMentions.none())
            for c in chunks[1:]:
                await message.channel.send(c, allowed_mentions=discord.AllowedMentions.none())
        except Exception as e:
            print(f'[gemini] 답장 실패: {e}')


async def setup(bot):
    await bot.add_cog(GeminiChat(bot))

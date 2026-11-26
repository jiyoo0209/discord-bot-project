'''
* 제미나이 대화 — 지정 채널에 글을 쓰면 Gemini 가 모두에게 보이는 답장으로 대답
*   램 절약: 공식 라이브러리 없이 discord.py 에 들어 있는 aiohttp 로 REST API 직접 호출
*   대화 기억: 채널 대화를 최근 MAX_TURNS 개만 기억 (누가 말했는지 닉네임 포함), IDLE_MIN 분 조용하면 잊음
*   사진·파일·스티커 등 글이 아닌 것만 올리면 "텍스트만 답장 가능해" 안내, 글과 같이 올리면 글에만 대답
*   사용 한도 초과(429) → "사용 한도가 초과되었습니다! ○월 ○일 ○시 ○분에 사용 가능합니다!"
*   모델: MODELS 의 안정 버전 모델을 차례로 시도 (붐빔·지연·없는 모델이면 다음으로), 마지막으로 성공한 모델을 먼저 씀
*         생각 단계 최소화, 모델 하나에 최대 WAIT 초, 전체 TOTAL 초 안에 답 못 하면 '바쁘대' 안내
*         봇 시작 시 이 키로 쓸 수 있는 Flash 모델 목록을 콘솔에 출력 (모델 이름이 바뀌었을 때 확인용)
*   필요: .env 에 GEMINI_API_KEY (선택: GEMINI_MODEL — 쉼표로 여러 개, 적으면 MODELS 대신 사용)
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
# 시도 순서 — 가볍고 빠른 안정 버전부터. '-latest' 별칭은 가장 새 모델이라 붐비는 일이 많아 맨 뒤에 둠
MODELS = [m.strip() for m in (os.getenv('GEMINI_MODEL') or '').split(',') if m.strip()] or [
    'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite', 'gemini-3.8-flash',
    'gemini-flash-lite-latest', 'gemini-flash-latest',
]
LIST_API = 'https://generativelanguage.googleapis.com/v1beta/models?pageSize=200'
WAIT = 20               # 모델 하나당 기다리는 최대 초
TOTAL = 45              # 한 질문에 전체로 기다리는 최대 초
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
        self.models = list(MODELS)  # 없는 모델(404)은 빠짐, 성공한 모델이 맨 앞으로
        self.no_think = set()       # '생각 최소화' 옵션을 지원 안 하는 모델

    async def cog_load(self):
        if not self.key:
            print('[gemini] GEMINI_API_KEY 가 없어서 대화 기능을 끕니다')
        # IPv4 로만 연결 — 호스팅 서버에서 IPv6 로 붙으려다 응답 없이 시간 초과되는 경우가 있음
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(family=socket.AF_INET),
            timeout=aiohttp.ClientTimeout(total=WAIT, sock_connect=10))
        if self.key:
            asyncio.create_task(self._log_models())

    async def _log_models(self):
        '''이 키로 쓸 수 있는 Flash 계열 모델을 콘솔에 한 번 출력'''
        try:
            async with self.session.get(LIST_API, headers={'x-goog-api-key': self.key}) as r:
                data = await r.json(content_type=None)
            names = [m['name'].split('/')[-1] for m in data.get('models', [])
                     if 'generateContent' in (m.get('supportedGenerationMethods') or []) and 'flash' in m['name']]
            print('[gemini] 사용 가능한 Flash 모델: ' + (', '.join(names) or f'(없음) {str(data)[:200]}'))
        except Exception as e:
            print(f'[gemini] 모델 목록 확인 실패: {type(e).__name__} {e!r}')

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    async def _ask(self, contents):
        '''모델을 차례로 시도: 붐빔(500/503)·시간 초과(0)·없는 모델(404)이면 다음 모델. 전체 TOTAL 초까지'''
        end = time.monotonic() + TOTAL
        status, data = 0, None
        for model in list(self.models):
            left = end - time.monotonic()
            if left < 3:
                break
            status, data = await self._post(model, contents, min(WAIT, left))
            if status == 404:
                print(f'[gemini] {model} 모델이 없어서 목록에서 뺌')
                if len(self.models) > 1:
                    self.models.remove(model)
                continue
            if status in (0, 500, 503):
                print(f'[gemini] {model} 붐빔/지연 {status} → 다음 모델')
                continue
            if status == 200 and self.models[0] != model:   # 잘 되는 모델을 다음부터 먼저
                self.models.remove(model)
                self.models.insert(0, model)
                print(f'[gemini] {model} 모델로 답함 (다음부터 이 모델 먼저)')
            return status, data
        return (503 if status == 404 else status), data

    async def _post(self, model, contents, wait=WAIT):
        body = {
            'systemInstruction': {'parts': [{'text': PERSONAS[PERSONA] + '\n' + RULES}]},
            'contents': contents,
            'generationConfig': {'maxOutputTokens': 1500, 'temperature': 0.8},
        }
        think = model not in self.no_think
        if think:   # 답하기 전 '생각' 단계를 최소로 → 훨씬 빨리 답함
            body['generationConfig']['thinkingConfig'] = {'thinkingLevel': 'minimal'}
        t0 = time.monotonic()
        try:
            async with self.session.post(API.format(model), json=body, headers={'x-goog-api-key': self.key},
                                         timeout=aiohttp.ClientTimeout(total=wait, sock_connect=10)) as r:
                data = await r.json(content_type=None)
                status = r.status
        except asyncio.TimeoutError:
            print(f'[gemini] {model} {wait:.0f}초 안에 응답 없음')
            return 0, None
        if status == 400 and think and 'thinking' in str(data).lower():
            self.no_think.add(model)   # 이 모델은 생각 최소화 옵션을 지원 안 함 → 빼고 다시
            return await self._post(model, contents, max(3, wait - (time.monotonic() - t0)))
        return status, data

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
            if status in (0, 500, 503):
                print(f'[gemini] 모든 모델 붐빔/지연 ({status})')
                await self._reply(message, '헉 지금 제미나이가 사람이 너무 많아서 바쁘대 ㅠㅠ 1~2분 뒤에 다시 말 걸어줘!!')
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

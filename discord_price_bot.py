#!/usr/bin/env python3
import asyncio
import os
import re

import discord
import requests
from discord import app_commands

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
DISCORD_ALLOWED_USER_ID = os.environ.get("DISCORD_ALLOWED_USER_ID", "").strip()
DISCORD_GUILD_ID = os.environ.get("DISCORD_GUILD_ID", "").strip()

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.environ.get(
    "GITHUB_REPO",
    "pingpingjinping/naver-series-set-checker",
).strip()
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip() or "main"

PRICE_WORKFLOW = "price.yml"
MANAGER_WORKFLOW = "check.yml"


def required(name: str, value: str) -> str:
    if not value:
        raise RuntimeError(f"{name} 환경변수가 필요합니다.")
    return value


def parse_id(name: str, value: str):
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name}은 숫자 ID여야 합니다.") from exc


required("DISCORD_BOT_TOKEN", DISCORD_BOT_TOKEN)
required("DISCORD_ALLOWED_USER_ID", DISCORD_ALLOWED_USER_ID)
required("GITHUB_TOKEN", GITHUB_TOKEN)

ALLOWED_USER_ID = parse_id("DISCORD_ALLOWED_USER_ID", DISCORD_ALLOWED_USER_ID)
GUILD_ID = parse_id("DISCORD_GUILD_ID", DISCORD_GUILD_ID)


def actions_url() -> str:
    return f"https://github.com/{GITHUB_REPO}/actions"


def dispatch_workflow(workflow: str, inputs: dict | None = None) -> None:
    url = (
        f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/"
        f"{workflow}/dispatches"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "WalnutPi-Discord-Price-Bot/1.0",
    }
    payload = {
        "ref": GITHUB_BRANCH,
        "inputs": inputs or {},
    }
    r = requests.post(url, headers=headers, json=payload, timeout=20)
    if r.status_code != 204:
        detail = (r.text or "").strip().replace("\n", " ")[:500]
        raise RuntimeError(f"GitHub Actions 호출 실패: HTTP {r.status_code} {detail}")


class PriceBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"Discord guild slash commands synced: {len(synced)}")
        else:
            synced = await self.tree.sync()
            print(f"Discord global slash commands synced: {len(synced)}")

    async def on_ready(self):
        print(f"Discord bot ready: {self.user} / allowed_user={ALLOWED_USER_ID}")


bot = PriceBot()


async def check_owner(interaction: discord.Interaction) -> bool:
    if interaction.user.id == ALLOWED_USER_ID:
        return True

    message = "이 명령은 등록된 사용자만 실행할 수 있어."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
    return False


async def run_action(
    interaction: discord.Interaction,
    workflow: str,
    inputs: dict,
    label: str,
):
    if not await check_owner(interaction):
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await asyncio.to_thread(dispatch_workflow, workflow, inputs)
    except Exception as exc:
        await interaction.followup.send(
            f"❌ {label} 요청 실패\n`{type(exc).__name__}: {exc}`",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        f"✅ {label} 요청을 GitHub Actions에 보냈어.\n<{actions_url()}>",
        ephemeral=True,
    )


@bot.tree.command(name="가격", description="등록된 상품의 가격을 지금 확인합니다.")
async def price_now(interaction: discord.Interaction):
    await run_action(
        interaction,
        PRICE_WORKFLOW,
        {"force_run": "true"},
        "가격 확인",
    )


@bot.tree.command(name="목록", description="현재 가격 추적 목록을 Discord로 전송합니다.")
async def price_list(interaction: discord.Interaction):
    await run_action(
        interaction,
        MANAGER_WORKFLOW,
        {"send_current_prices": "true"},
        "현재 가격 목록 전송",
    )


@bot.tree.command(name="추가", description="가격 추적 URL을 추가합니다.")
@app_commands.describe(url="Steam/STOVE 상품 URL. 여러 개면 공백이나 쉼표로 구분 가능")
async def add_url(interaction: discord.Interaction, url: str):
    values = [x for x in re.split(r"[\s,]+", url.strip()) if x]
    if not values or any(not x.startswith(("http://", "https://")) for x in values):
        await interaction.response.send_message(
            "URL 형식이 이상해. http:// 또는 https:// 주소를 입력해줘.",
            ephemeral=True,
        )
        return

    await run_action(
        interaction,
        MANAGER_WORKFLOW,
        {"add_urls": url.strip()},
        f"가격 추적 URL {len(values)}개 추가",
    )


@bot.tree.command(name="삭제", description="현재 가격 목록의 번호로 추적 항목을 삭제합니다.")
@app_commands.describe(numbers="삭제할 번호. 예: 3 또는 1,3,5")
async def remove_url(interaction: discord.Interaction, numbers: str):
    tokens = [x for x in re.split(r"[\s,]+", numbers.strip()) if x]
    if not tokens or any(not x.isdigit() or int(x) < 1 for x in tokens):
        await interaction.response.send_message(
            "삭제 번호는 1 이상의 숫자로 입력해줘. 예: 3 또는 1,3,5",
            ephemeral=True,
        )
        return

    normalized = ",".join(dict.fromkeys(tokens))
    await run_action(
        interaction,
        MANAGER_WORKFLOW,
        {"remove_numbers": normalized},
        f"가격 추적 #{normalized} 삭제",
    )


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN, log_handler=None)

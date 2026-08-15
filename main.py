import asyncio
import datetime
import json
import os
from threading import Thread
import unicodedata
import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask
from supabase import create_client, Client

# ==========================================
# 1. Renderスリープ防止用 Webサーバー (Flask)
# ==========================================
app = Flask("")

@app.route("/")
def home():
    return "Bot is alive and running on Render!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()


# ==========================================
# 2. Supabase データベース接続設定
# ==========================================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[CRITICAL] SUPABASE_URL または SUPABASE_KEY が設定されていません！")
    supabase: Client = None
else:
    # URLの末尾スラッシュを除去
    SUPABASE_URL = SUPABASE_URL.rstrip("/")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ==========================================
# 3. Discord Botの設定
# ==========================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

ADMIN_ROLE_NAME = "スーパーモデレーター"

DEFAULT_CONFIG = {
    "max_points": 3,
    "timeout_minutes": 10,
    "timeout_multiplier": 2.0,
    "notify_channel_id": None,
    "notify_enabled": True,
    "exempt_role_name": None,
    "block_user_ids": [],
}

IGNORE_CHARS = [" ","。","、","〇", " ", "_", "-", ".", ",", "/", "・", "★", "☆", "〜", "~", "!", "?", "│", "|"]


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).lower()
    katakana_chars = []
    for ch in text:
        if "ぁ" <= ch <= "ん":
            katakana_chars.append(chr(ord(ch) + 0x60))
        else:
            katakana_chars.append(ch)
    text = "".join(katakana_chars)
    for char in IGNORE_CHARS:
        text = text.replace(char, "")
    return text


def has_admin_role():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False
        user_role_names = [role.name for role in interaction.user.roles]
        if ADMIN_ROLE_NAME in user_role_names:
            return True
        raise app_commands.MissingRole(ADMIN_ROLE_NAME)
    return app_commands.check(predicate)


# ==========================================
# 4. 非同期対応 Supabase DB操作関数
# ==========================================
async def async_db_run(func, *args, **kwargs):
    """DB操作でBotのメインスレッドを止めないための非同期ラッパー"""
    if not supabase:
        return None
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except Exception as e:
        print(f"[DB Error] {e}")
        return None


def _get_config_sync():
    res = supabase.table("bot_config").select("data").eq("id", "main").execute()
    if res.data:
        config = res.data[0]["data"]
        for k, v in DEFAULT_CONFIG.items():
            if k not in config:
                config[k] = v
        return config
    return DEFAULT_CONFIG

async def get_config():
    if not supabase:
        return DEFAULT_CONFIG
    res = await async_db_run(_get_config_sync)
    return res if res else DEFAULT_CONFIG


def _save_config_sync(config_data):
    supabase.table("bot_config").upsert({"id": "main", "data": config_data}).execute()

async def save_config(config_data):
    await async_db_run(_save_config_sync, config_data)


def _get_ng_words_sync():
    res = supabase.table("ng_words").select("word").execute()
    return [row["word"] for row in res.data]

async def get_ng_words():
    if not supabase:
        return []
    res = await async_db_run(_get_ng_words_sync)
    return res if res is not None else []


def _add_ng_word_sync(word):
    supabase.table("ng_words").upsert({"word": word}).execute()

async def add_ng_word_db(word: str):
    await async_db_run(_add_ng_word_sync, word)


def _remove_ng_word_sync(word):
    supabase.table("ng_words").delete().eq("word", word).execute()

async def remove_ng_word_db(word: str):
    await async_db_run(_remove_ng_word_sync, word)


def _get_user_points_sync(user_id):
    res = supabase.table("user_points").select("points").eq("user_id", str(user_id)).execute()
    if res.data:
        return res.data[0]["points"]
    return 0

async def get_user_points(user_id: str) -> int:
    if not supabase:
        return 0
    res = await async_db_run(_get_user_points_sync, user_id)
    return res if res is not None else 0


def _set_user_points_sync(user_id, points):
    supabase.table("user_points").upsert({"user_id": str(user_id), "points": points}).execute()

async def set_user_points(user_id: str, points: int):
    await async_db_run(_set_user_points_sync, user_id, points)


async def send_ng_list_update(guild: discord.Guild, title_text: str):
    config = await get_config()
    if not config.get("notify_enabled", True):
        return

    channel_id = config.get("notify_channel_id")
    if not channel_id:
        return

    try:
        channel_id_int = int(channel_id)
        channel = guild.get_channel(channel_id_int) or await guild.fetch_channel(channel_id_int)
    except Exception as e:
        print(f"[Error] 通知用チャンネル取得失敗: {e}")
        return

    if channel:
        ng_words = await get_ng_words()
        word_list = "\n".join([f"・ {w}" for w in ng_words]) if ng_words else "（現在登録されているNGワードはありません）"
        embed = discord.Embed(
            title=f"📢 {title_text}",
            description=f"**現在のNGワード一覧:**\n{word_list}",
            color=discord.Color.blue()
        )
        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"[Error] 通知送信失敗: {e}")


# ==========================================
# 5. Botのイベント処理
# ==========================================
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"[Error] Sync failed: {e}")
    print(f"Logged in as: {bot.user.name}")


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    config = await get_config()
    block_user_ids = [int(uid) for uid in config.get("block_user_ids", [])]
    is_individually_blocked = message.author.id in block_user_ids

    # 個別ブロックされていなければ、管理者/免除ロールをスルー
    if not is_individually_blocked and isinstance(message.author, discord.Member):
        exempt_role_name = config.get("exempt_role_name")
        user_role_names = [role.name for role in message.author.roles]

        if (exempt_role_name and exempt_role_name in user_role_names) or (ADMIN_ROLE_NAME in user_role_names):
            await bot.process_commands(message)
            return

    ng_words = await get_ng_words()
    cleaned_message = clean_text(message.content)
    contains_ng_word = False

    for ng_word in ng_words:
        cleaned_ng = clean_text(ng_word)
        if cleaned_ng and cleaned_ng in cleaned_message:
            contains_ng_word = True
            break

    if contains_ng_word:
        user_id = str(message.author.id)
        current_points = await get_user_points(user_id)
        total_points = current_points + 1
        await set_user_points(user_id, total_points)

        try:
            await message.delete()
        except Exception:
            pass

        await message.channel.send(
            f"{message.author.mention} 不適切なワードを検知しました。（通算: {total_points}回目）",
            delete_after=5
        )

        max_points = config.get("max_points", 3)
        base_minutes = config.get("timeout_minutes", 5)
        multiplier = config.get("timeout_multiplier", 2.0)

        if total_points % max_points == 0:
            timeout_count = total_points // max_points
            calc_minutes = int(base_minutes * (multiplier ** (timeout_count - 1)))
            calc_minutes = min(calc_minutes, 40320)

            duration = datetime.timedelta(minutes=calc_minutes)
            try:
                await message.author.timeout(
                    duration, reason=f"NGワード通算{total_points}回到達（TO: {timeout_count}回目）"
                )
                await message.channel.send(
                    f"⚠️ {message.author.mention} が通算 {total_points} 回目の違反に達しました。\n"
                    f"（TO通算 {timeout_count} 回目のため、**{calc_minutes}分間** タイムアウトされました）"
                )
            except discord.errors.Forbidden:
                await message.channel.send("❌ タイムアウト権限がないか、対象ユーザーの権限がBotより上位です。")
            except Exception as e:
                print(f"[Error] TOエラー: {e}")

    await bot.process_commands(message)


# ==========================================
# 6. スラッシュコマンド
# ==========================================
@bot.tree.command(name="block_user", description="【管理者専用】個別ブロック登録")
@has_admin_role()
async def block_user(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)  # 3秒タイムアウト防止
    config = await get_config()
    block_list = [int(uid) for uid in config.get("block_user_ids", [])]

    if user.id in block_list:
        await interaction.followup.send(f"{user.mention} は既に個別ブロック対象です。", ephemeral=True)
        return

    block_list.append(user.id)
    config["block_user_ids"] = block_list
    await save_config(config)

    await interaction.followup.send(f"🚫 {user.mention} を個別ブロック対象に指定しました。", ephemeral=True)


@bot.tree.command(name="unblock_user", description="【管理者専用】個別ブロック解除")
@has_admin_role()
async def unblock_user(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    config = await get_config()
    block_list = [int(uid) for uid in config.get("block_user_ids", [])]

    if user.id not in block_list:
        await interaction.followup.send(f"{user.mention} はブロック対象ではありません。", ephemeral=True)
        return

    block_list.remove(user.id)
    config["block_user_ids"] = block_list
    await save_config(config)

    await interaction.followup.send(f"✅ {user.mention} の個別ブロックを解除しました。", ephemeral=True)


@bot.tree.command(name="my_points", description="自分の通算警告回数を確認")
async def my_points(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    total_points = await get_user_points(str(interaction.user.id))
    await interaction.followup.send(f"📊 {interaction.user.mention} さんの通算警告回数は **{total_points} 回** です。", ephemeral=True)


@bot.tree.command(name="check_points", description="【管理者専用】指定ユーザーの警告回数確認")
@has_admin_role()
async def check_points(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    total_points = await get_user_points(str(user.id))
    await interaction.followup.send(f"🔍 {user.mention} さんの通算警告回数は **{total_points} 回** です。", ephemeral=True)


@bot.tree.command(name="reset_points", description="【管理者専用】警告回数をリセット")
@has_admin_role()
async def reset_points(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    await set_user_points(str(user.id), 0)
    await interaction.followup.send(f"🔄 {user.mention} さんの通算警告回数を 0 回にリセットしました。", ephemeral=True)


@bot.tree.command(name="add_ng", description="【管理者専用】NGワード追加")
@has_admin_role()
async def add_ng_word(interaction: discord.Interaction, word: str):
    await interaction.response.defer(ephemeral=True)
    ng_words = await get_ng_words()
    if word in ng_words:
        await interaction.followup.send(f"「{word}」はすでに登録されています。", ephemeral=True)
        return

    await add_ng_word_db(word)
    await interaction.followup.send(f"✅ NGワードに「{word}」を追加しました。", ephemeral=True)

    if interaction.guild:
        await send_ng_list_update(interaction.guild, f"NGワードが追加されました（追加: {word}）")


@bot.tree.command(name="remove_ng", description="【管理者専用】NGワード削除")
@has_admin_role()
async def remove_ng_word(interaction: discord.Interaction, word: str):
    await interaction.response.defer(ephemeral=True)
    ng_words = await get_ng_words()
    if word not in ng_words:
        await interaction.followup.send(f"「{word}」は登録されていません。", ephemeral=True)
        return

    await remove_ng_word_db(word)
    await interaction.followup.send(f"🗑️ NGワードから「{word}」を削除しました。", ephemeral=True)

    if interaction.guild:
        await send_ng_list_update(interaction.guild, f"NGワードが削除されました（削除: {word}）")


@bot.tree.command(name="list_ng", description="登録中のNGワード一覧")
@has_admin_role()
async def list_ng_words(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    ng_words = await get_ng_words()
    if not ng_words:
        await interaction.followup.send("現在登録されているNGワードはありません。", ephemeral=True)
        return

    word_list = "\n".join([f"・ {w}" for w in ng_words])
    await interaction.followup.send(f"📋 **現在のNGワード一覧:**\n{word_list}", ephemeral=True)


@bot.tree.command(name="set_exempt_role", description="【管理者専用】免除役職の設定")
@has_admin_role()
async def set_exempt_role(interaction: discord.Interaction, role: discord.Role):
    await interaction.response.defer(ephemeral=True)
    config = await get_config()
    config["exempt_role_name"] = role.name
    await save_config(config)
    await interaction.followup.send(f"🛡️ 免除対象役職を **{role.name}** に設定しました。", ephemeral=True)


@bot.tree.command(name="set_channel", description="【管理者専用】通知チャンネルの設定")
@has_admin_role()
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    config = await get_config()
    config["notify_channel_id"] = channel.id
    await save_config(config)
    await interaction.followup.send(f"📢 通知チャンネルを {channel.mention} に設定しました。", ephemeral=True)


@bot.tree.command(name="toggle_notify", description="【管理者専用】自動通知のON/OFF")
@has_admin_role()
async def toggle_notify(interaction: discord.Interaction, enable: bool):
    await interaction.response.defer(ephemeral=True)
    config = await get_config()
    config["notify_enabled"] = enable
    await save_config(config)
    status_str = "有効（ON）" if enable else "無効（OFF）"
    await interaction.followup.send(f"⚙️ 自動通知を **{status_str}** に設定しました。", ephemeral=True)


@bot.tree.command(name="set_timeout_rules", description="【管理者専用】タイムアウト規則の設定")
@has_admin_role()
async def set_timeout_rules(interaction: discord.Interaction, max_points: int, minutes: int, multiplier: float = 2.0):
    await interaction.response.defer(ephemeral=True)
    if max_points <= 0 or minutes <= 0 or multiplier < 1.0:
        await interaction.followup.send("正しい数値を指定してください。", ephemeral=True)
        return

    config = await get_config()
    config["max_points"] = max_points
    config["timeout_minutes"] = minutes
    config["timeout_multiplier"] = multiplier
    await save_config(config)

    await interaction.followup.send(
        f"⚙️ タイムアウト設定を変更しました：\n"
        f"・ **トリガー:** {max_points}回ごと\n"
        f"・ **初回時間:** {minutes}分\n"
        f"・ **倍率:** {multiplier}倍",
        ephemeral=True
    )


@bot.tree.command(name="show_config", description="【管理者専用】設定確認")
@has_admin_role()
async def show_config(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    config = await get_config()
    channel_id = config.get("notify_channel_id")
    channel_mention = f"<#{channel_id}>" if channel_id else "未設定"
    notify_status = "ON (有効)" if config.get("notify_enabled", True) else "OFF (無効)"
    exempt_role = config.get("exempt_role_name") or "未設定"

    block_user_ids = config.get("block_user_ids", [])
    blocked_mentions = " ".join([f"<@{uid}>" for uid in block_user_ids]) if block_user_ids else "なし"
    multiplier = config.get("timeout_multiplier", 2.0)

    msg = (
        f"⚙️ **現在の設定状況 (DB同期中):**\n"
        f"・ **タイムアウト発生基準:** {config.get('max_points', 3)} 回ごと\n"
        f"・ **初回タイムアウト時間:** {config.get('timeout_minutes', 5)} 分間\n"
        f"・ **重ねがけ倍率:** {multiplier} 倍\n"
        f"・ **免除対象の役職:** `{exempt_role}`\n"
        f"・ **個別ブロックユーザー:** {blocked_mentions}\n"
        f"・ **通知チャンネル:** {channel_mention}\n"
        f"・ **自動通知機能:** {notify_status}"
    )
    await interaction.followup.send(msg, ephemeral=True)


# ==========================================
# 7. 実行
# ==========================================
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    if TOKEN:
        keep_alive()
        bot.run(TOKEN)

import asyncio
import datetime
import json
import os
from threading import Thread
import unicodedata
import urllib.request
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

def keep_alive():
    port = int(os.environ.get("PORT", 10000))
    t = Thread(target=lambda: app.run(host="0.0.0.0", port=port))
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
    SUPABASE_URL = SUPABASE_URL.rstrip("/").replace("/rest/v1", "")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ==========================================
# 3. Discord Botの設定 & 共通ヘルパー
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

ADMIN_ROLE_NAME = "モデレーター"

DEFAULT_CONFIG = {
    "max_points": 3,
    "timeout_minutes": 5,
    "timeout_multiplier": 2.0,
    "notify_channel_id": None,
    "notify_enabled": True,
    "exempt_role_name": None,
    "block_user_ids": [],
}

IGNORE_CHARS = [" ", " ", "_", "-", ".", ",", "/", "・", "★", "☆", "〜", "~", "!", "?", "│", "|"]

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).lower()
    katakana = [chr(ord(c) + 0x60) if "ぁ" <= c <= "ん" else c for c in text]
    text = "".join(katakana)
    for char in IGNORE_CHARS:
        text = text.replace(char, "")
    return text

def has_admin_role():
    async def predicate(interaction: discord.Interaction) -> bool:
        if isinstance(interaction.user, discord.Member):
            if ADMIN_ROLE_NAME in [r.name for r in interaction.user.roles]:
                return True
        raise app_commands.MissingRole(ADMIN_ROLE_NAME)
    return app_commands.check(predicate)


# ==========================================
# 4. 非同期対応 Supabase DB操作関数
# ==========================================
async def async_db_run(func, *args, **kwargs):
    if not supabase:
        return None
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except Exception as e:
        print(f"[DB Error] {e}")
        return None

# Config
async def get_config():
    def _sync():
        res = supabase.table("bot_config").select("data").eq("id", "main").execute()
        if res.data:
            cfg = res.data[0]["data"]
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        return DEFAULT_CONFIG
    res = await async_db_run(_sync)
    return res if res else DEFAULT_CONFIG

async def save_config(config_data):
    await async_db_run(lambda: supabase.table("bot_config").upsert({"id": "main", "data": config_data}).execute())

# Words (NG / Exempt)
async def get_words(table_name: str):
    res = await async_db_run(lambda: supabase.table(table_name).select("word").execute())
    return [row["word"] for row in res.data] if res and res.data else []

async def add_word(table_name: str, word: str):
    await async_db_run(lambda: supabase.table(table_name).upsert({"word": word}).execute())

async def remove_word(table_name: str, word: str):
    await async_db_run(lambda: supabase.table(table_name).delete().eq("word", word).execute())

# User Points
async def get_user_points(user_id: str) -> int:
    res = await async_db_run(lambda: supabase.table("user_points").select("points").eq("user_id", str(user_id)).execute())
    return res.data[0]["points"] if res and res.data else 0

async def set_user_points(user_id: str, points: int):
    await async_db_run(lambda: supabase.table("user_points").upsert({"user_id": str(user_id), "points": points}).execute())

# Notification
async def send_ng_list_update(guild: discord.Guild, title_text: str):
    config = await get_config()
    if not config.get("notify_enabled", True) or not config.get("notify_channel_id"):
        return

    try:
        ch_id = int(config["notify_channel_id"])
        channel = guild.get_channel(ch_id) or await guild.fetch_channel(ch_id)
        if channel:
            ng_words = await get_words("ng_words")
            word_list = "\n".join([f"・ {w}" for w in ng_words]) if ng_words else "（現在登録されているNGワードはありません）"
            embed = discord.Embed(
                title=f"📢 {title_text}",
                description=f"**現在のNGワード一覧:**\n{word_list}",
                color=discord.Color.blue()
            )
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
    if message.author.bot or not message.guild:
        return

    config = await get_config()
    block_user_ids = [int(uid) for uid in config.get("block_user_ids", [])]
    is_individually_blocked = message.author.id in block_user_ids

    # 免除判定 (管理者または設定された免除ロール保持者)
    is_exempt_user = False
    if not is_individually_blocked and isinstance(message.author, discord.Member):
        user_roles = [r.name for r in message.author.roles]
        exempt_role = config.get("exempt_role_name")
        if (exempt_role and exempt_role in user_roles) or (ADMIN_ROLE_NAME in user_roles):
            is_exempt_user = True

    # テキスト正規化 & 除外ワード処理
    cleaned_message = clean_text(message.content)
    for exempt_word in await get_words("exempt_words"):
        c_exempt = clean_text(exempt_word)
        if c_exempt and c_exempt in cleaned_message:
            cleaned_message = cleaned_message.replace(c_exempt, "***")

    # NGワード判定
    contains_ng = any(
        clean_text(w) in cleaned_message 
        for w in await get_words("ng_words") 
        if clean_text(w)
    )

    if contains_ng:
        # メッセージ消去は共通
        try:
            await message.delete()
        except Exception:
            pass

        # 免除ユーザー: メッセージ削除＋警告のみ（TOスキップ）
        if is_exempt_user:
            await message.channel.send(
                f"{message.author.mention} 不適切なワードが検知されたため削除しました。（免除対象のためタイムアウトは適用されません）",
                delete_after=5
            )
            await bot.process_commands(message)
            return

        # 一般ユーザー: ポイント加算＋TO処理
        user_id = str(message.author.id)
        total_points = await get_user_points(user_id) + 1
        await set_user_points(user_id, total_points)

        await message.channel.send(
            f"{message.author.mention} 不適切なワードを検知しました。（通算: {total_points}回目）",
            delete_after=5
        )

        max_points = config.get("max_points", 3)
        if total_points % max_points == 0:
            timeout_count = total_points // max_points
            calc_minutes = min(
                int(config.get("timeout_minutes", 5) * (config.get("timeout_multiplier", 2.0) ** (timeout_count - 1))),
                40320
            )
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

# --- 除外ワード (ホワイトリスト) ---
@bot.tree.command(name="add_exempt", description="【管理者専用】除外ワード（誤検知防止用）追加")
@has_admin_role()
async def add_exempt_word_cmd(interaction: discord.Interaction, word: str):
    await interaction.response.defer(ephemeral=True)
    if word in await get_words("exempt_words"):
        await interaction.followup.send(f"「{word}」はすでに除外リストに登録されています。", ephemeral=True)
        return
    await add_word("exempt_words", word)
    await interaction.followup.send(f"🛡️ 除外ワードに「{word}」を追加しました。", ephemeral=True)

@bot.tree.command(name="remove_exempt", description="【管理者専用】除外ワード削除")
@has_admin_role()
async def remove_exempt_word_cmd(interaction: discord.Interaction, word: str):
    await interaction.response.defer(ephemeral=True)
    if word not in await get_words("exempt_words"):
        await interaction.followup.send(f"「{word}」は除外リストにありません。", ephemeral=True)
        return
    await remove_word("exempt_words", word)
    await interaction.followup.send(f"🗑️ 除外ワードから「{word}」を削除しました。", ephemeral=True)

@bot.tree.command(name="list_exempt", description="登録中の除外ワード一覧")
@has_admin_role()
async def list_exempt_words_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    words = await get_words("exempt_words")
    msg = "🛡️ **現在の除外ワード一覧:**\n" + ("\n".join([f"・ {w}" for w in words]) if words else "（未登録）")
    await interaction.followup.send(msg, ephemeral=True)


# --- インポート (Raw URL対応) ---
@bot.tree.command(name="import_ng", description="【管理者専用】GitHub等のRaw URLからNGワードを一括登録")
@has_admin_role()
async def import_ng_words(interaction: discord.Interaction, url: str):
    await interaction.response.defer(ephemeral=True)
    if "github.com" in url and "raw.githubusercontent.com" not in url:
        await interaction.followup.send("❌ 通常のGitHubページURLです。「Raw」ボタンを押した後のURLを指定してください。", ephemeral=True)
        return

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read().decode('utf-8')
    except Exception as e:
        await interaction.followup.send(f"❌ ファイル取得失敗: {e}", ephemeral=True)
        return

    try:
        data = json.loads(content)
        words = [str(item).strip() for item in data if str(item).strip()] if isinstance(data, list) else []
    except json.JSONDecodeError:
        words = [line.strip() for line in content.splitlines() if line.strip()]

    if not words:
        await interaction.followup.send("❌ 有効な単語が見つかりませんでした。", ephemeral=True)
        return

    existing = await get_words("ng_words")
    added = 0
    for w in words:
        if w not in existing:
            await add_word("ng_words", w)
            added += 1

    await interaction.followup.send(f"✅ 一括登録完了！\n・ 読み込み: {len(words)} 件\n・ 新規追加: {added} 件", ephemeral=True)
    if interaction.guild and added > 0:
        await send_ng_list_update(interaction.guild, f"NGワードが {added} 件追加されました")


# --- 通常NGワード・管理コマンド ---
@bot.tree.command(name="add_ng", description="【管理者専用】NGワード追加")
@has_admin_role()
async def add_ng_word(interaction: discord.Interaction, word: str):
    await interaction.response.defer(ephemeral=True)
    if word in await get_words("ng_words"):
        await interaction.followup.send(f"「{word}」はすでに登録されています。", ephemeral=True)
        return
    await add_word("ng_words", word)
    await interaction.followup.send(f"✅ NGワードに「{word}」を追加しました。", ephemeral=True)
    if interaction.guild:
        await send_ng_list_update(interaction.guild, f"NGワード追加（追加: {word}）")

@bot.tree.command(name="remove_ng", description="【管理者専用】NGワード削除")
@has_admin_role()
async def remove_ng_word(interaction: discord.Interaction, word: str):
    await interaction.response.defer(ephemeral=True)
    if word not in await get_words("ng_words"):
        await interaction.followup.send(f"「{word}」は登録されていません。", ephemeral=True)
        return
    await remove_word("ng_words", word)
    await interaction.followup.send(f"🗑️ NGワードから「{word}」を削除しました。", ephemeral=True)
    if interaction.guild:
        await send_ng_list_update(interaction.guild, f"NGワード削除（削除: {word}）")

@bot.tree.command(name="list_ng", description="登録中のNGワード一覧")
@has_admin_role()
async def list_ng_words(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    words = await get_words("ng_words")
    msg = "📋 **現在のNGワード一覧:**\n" + ("\n".join([f"・ {w}" for w in words]) if words else "（未登録）")
    await interaction.followup.send(msg, ephemeral=True)

@bot.tree.command(name="block_user", description="【管理者専用】個別ブロック登録")
@has_admin_role()
async def block_user(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    config = await get_config()
    blocks = [int(u) for u in config.get("block_user_ids", [])]
    if user.id in blocks:
        await interaction.followup.send(f"{user.mention} は既にブロック対象です。", ephemeral=True)
        return
    blocks.append(user.id)
    config["block_user_ids"] = blocks
    await save_config(config)
    await interaction.followup.send(f"🚫 {user.mention} を個別ブロック対象に指定しました。", ephemeral=True)

@bot.tree.command(name="unblock_user", description="【管理者専用】個別ブロック解除")
@has_admin_role()
async def unblock_user(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    config = await get_config()
    blocks = [int(u) for u in config.get("block_user_ids", [])]
    if user.id not in blocks:
        await interaction.followup.send(f"{user.mention} はブロック対象ではありません。", ephemeral=True)
        return
    blocks.remove(user.id)
    config["block_user_ids"] = blocks
    await save_config(config)
    await interaction.followup.send(f"✅ {user.mention} の個別ブロックを解除しました。", ephemeral=True)

@bot.tree.command(name="my_points", description="自分の通算警告回数を確認")
async def my_points(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    pts = await get_user_points(str(interaction.user.id))
    await interaction.followup.send(f"📊 {interaction.user.mention} さんの通算警告回数は **{pts} 回** です。", ephemeral=True)

@bot.tree.command(name="check_points", description="【管理者専用】指定ユーザーの警告回数確認")
@has_admin_role()
async def check_points(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    pts = await get_user_points(str(user.id))
    await interaction.followup.send(f"🔍 {user.mention} さんの通算警告回数は **{pts} 回** です。", ephemeral=True)

@bot.tree.command(name="reset_points", description="【管理者専用】警告回数をリセット")
@has_admin_role()
async def reset_points(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    await set_user_points(str(user.id), 0)
    await interaction.followup.send(f"🔄 {user.mention} さんの通算警告回数を 0 回にリセットしました。", ephemeral=True)

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
    status = "有効（ON）" if enable else "無効（OFF）"
    await interaction.followup.send(f"⚙️ 自動通知を **{status}** に設定しました。", ephemeral=True)

@bot.tree.command(name="set_timeout_rules", description="【管理者専用】タイムアウト規則の設定")
@has_admin_role()
async def set_timeout_rules(interaction: discord.Interaction, max_points: int, minutes: int, multiplier: float = 2.0):
    await interaction.response.defer(ephemeral=True)
    if max_points <= 0 or minutes <= 0 or multiplier < 1.0:
        await interaction.followup.send("正しい数値を指定してください。", ephemeral=True)
        return
    config = await get_config()
    config.update({"max_points": max_points, "timeout_minutes": minutes, "timeout_multiplier": multiplier})
    await save_config(config)
    await interaction.followup.send(
        f"⚙️ タイムアウト設定変更：\n・ **トリガー:** {max_points}回ごと\n・ **初回:** {minutes}分\n・ **倍率:** {multiplier}倍",
        ephemeral=True
    )

@bot.tree.command(name="show_config", description="【管理者専用】設定確認")
@has_admin_role()
async def show_config(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    config = await get_config()
    ch_id = config.get("notify_channel_id")
    ch_str = f"<#{ch_id}>" if ch_id else "未設定"
    notify_str = "ON (有効)" if config.get("notify_enabled", True) else "OFF (無効)"
    exempt_role = config.get("exempt_role_name") or "未設定"
    blocks = config.get("block_user_ids", [])
    blocked_str = " ".join([f"<@{uid}>" for uid in blocks]) if blocks else "なし"

    msg = (
        f"⚙️ **現在の設定状況 (DB同期中):**\n"
        f"・ **タイムアウト発生基準:** {config.get('max_points', 3)} 回ごと\n"
        f"・ **初回タイムアウト時間:** {config.get('timeout_minutes', 5)} 分間\n"
        f"・ **重ねがけ倍率:** {config.get('timeout_multiplier', 2.0)} 倍\n"
        f"・ **免除対象の役職:** `{exempt_role}`\n"
        f"・ **個別ブロックユーザー:** {blocked_str}\n"
        f"・ **通知チャンネル:** {ch_str}\n"
        f"・ **自動通知機能:** {notify_str}"
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

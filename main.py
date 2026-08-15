import datetime
import json
import os
from threading import Thread
import unicodedata
import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask

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
# 2. Discord Botの設定 & 許可設定
# ==========================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ファイルパス定義
NG_WORDS_FILE = "ng_words.json"
POINTS_FILE = "user_points.json"
CONFIG_FILE = "config.json"

# ★ Bot管理用コマンドを実行できるロール名
ADMIN_ROLE_NAME = "スーパーモデレーター"

# デフォルト設定値（初期状態では免除ロールなし）
DEFAULT_CONFIG = {
    "max_points": 3,
    "timeout_minutes": 10,
    "timeout_multiplier": 2.0,  # TO重なるごとの倍率
    "notify_channel_id": None,
    "notify_enabled": True,
    "exempt_role_name": None,  # 明示的に設定されるまで免除なし
    "block_user_ids": [],  # 個別ブロック対象のユーザーIDリスト
}

# 回避に使われやすいノイズ記号・スペースのリスト
IGNORE_CHARS = [
    " ",
    "。",
    "、",
    "〇",
    " ",
    "_",
    "-",
    ".",
    ",",
    "/",
    "・",
    "★",
    "☆",
    "〜",
    "~",
    "!",
    "?",
    "│",
    "|",
]


# ==========================================
# 3. テキスト前処理（正規表現不使用の対策）
# ==========================================
def clean_text(text: str) -> str:
    """大文字小文字・全角半角・ひらがなカタカナ・記号挟みを統一・除去する関数"""
    if not text:
        return ""

    # 1. 全角英数・記号を半角化 ＆ 大文字を小文字化
    text = unicodedata.normalize("NFKC", text).lower()

    # 2. ひらがなをカタカナに統一（Unicodeのコードポイントの差分を利用）
    katakana_chars = []
    for ch in text:
        if "ぁ" <= ch <= "ん":
            katakana_chars.append(chr(ord(ch) + 0x60))
        else:
            katakana_chars.append(ch)
    text = "".join(katakana_chars)

    # 3. 無視する記号やスペースの除去
    for char in IGNORE_CHARS:
        text = text.replace(char, "")

    return text


# ==========================================
# 4. カスタム権限チェック（管理者ロール判定）
# ==========================================
def has_admin_role():
    """管理用コマンドの実行権限をチェック"""

    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False

        user_role_names = [role.name for role in interaction.user.roles]
        if ADMIN_ROLE_NAME in user_role_names:
            return True

        raise app_commands.MissingRole(ADMIN_ROLE_NAME)

    return app_commands.check(predicate)


# ==========================================
# 5. JSONファイル操作用のヘルパー関数
# ==========================================
def load_json(filepath, default_value):
    if not os.path.exists(filepath):
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(default_value, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[Error] JSON作成失敗 ({filepath}): {e}")
        return default_value

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Error] JSON読み込み失敗 ({filepath}): {e}")
        return default_value


def save_json(filepath, data):
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"[Error] JSON保存失敗 ({filepath}): {e}")


def get_config():
    config = load_json(CONFIG_FILE, DEFAULT_CONFIG)
    for key, value in DEFAULT_CONFIG.items():
        if key not in config:
            config[key] = value
    return config


async def send_ng_list_update(guild: discord.Guild, title_text: str):
    config = get_config()
    if not config.get("notify_enabled", True):
        return

    channel_id = config.get("notify_channel_id")
    if not channel_id:
        return

    try:
        channel_id_int = int(channel_id)
        channel = guild.get_channel(channel_id_int)
        if channel is None:
            channel = await guild.fetch_channel(channel_id_int)
    except Exception as e:
        print(f"[Error] 通知用チャンネルの取得失敗 (ID: {channel_id}): {e}")
        return

    if channel:
        ng_words = load_json(NG_WORDS_FILE, [])
        word_list = (
            "\n".join([f"・ {w}" for w in ng_words])
            if ng_words
            else "（現在登録されているNGワードはありません）"
        )

        embed = discord.Embed(
            title=f"📢 {title_text}",
            description=f"**現在のNGワード一覧:**\n{word_list}",
            color=discord.Color.blue(),
        )
        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"[Error] 通知メッセージの送信に失敗しました: {e}")


# ==========================================
# 6. Botのイベント処理
# ==========================================
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"[Error] Command sync failed: {e}")

    print("----------------------------------------")
    print(f"Logged in as: {bot.user.name} (ID: {bot.user.id})")
    print("Bot is ready and listening!")
    print("----------------------------------------")


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    config = get_config()

    # ★【修正点1】IDを型（int/str両対応）で確実に判定
    block_user_ids = [int(uid) for uid in config.get("block_user_ids", [])]
    is_individually_blocked = message.author.id in block_user_ids

    # ★【修正点2】免除・管理者スルー判定ロジックの最優先ガード
    # 個別ブロックされているユーザーは、管理者ロール・免除ロール・サーバー管理者権限があっても絶対にスルーさせない
    if not is_individually_blocked:
        if isinstance(message.author, discord.Member):
            exempt_role_name = config.get("exempt_role_name")
            user_role_names = [role.name for role in message.author.roles]

            # 免除判定 (ロール名が一致、または管理用ロール名と一致する場合)
            is_exempt_role = (
                exempt_role_name and (exempt_role_name in user_role_names)
            )
            is_admin_role = ADMIN_ROLE_NAME in user_role_names

            if is_exempt_role or is_admin_role:
                await bot.process_commands(message)
                return

    ng_words = load_json(NG_WORDS_FILE, [])
    user_points = load_json(POINTS_FILE, {})

    max_points = config.get("max_points", 3)
    base_minutes = config.get("timeout_minutes", 5)
    multiplier = config.get("timeout_multiplier", 2.0)

    # --- NGワード判定 ---
    cleaned_message = clean_text(message.content)
    contains_ng_word = False

    for ng_word in ng_words:
        cleaned_ng = clean_text(ng_word)
        if cleaned_ng and cleaned_ng in cleaned_message:
            contains_ng_word = True
            break

    if contains_ng_word:
        user_id = str(message.author.id)

        # 累計回数を加算
        total_points = user_points.get(user_id, 0) + 1
        user_points[user_id] = total_points
        save_json(POINTS_FILE, user_points)

        try:
            await message.delete()
        except discord.errors.Forbidden:
            pass
        except discord.errors.HTTPException:
            pass

        # 検出時に通算（累計）回数を表示
        await message.channel.send(
            f"{message.author.mention} 不適切なワードを検知しました。（通算: {total_points}回目）",
            delete_after=5,
        )

        # 規定回数ごとのタイムアウト処理
        if total_points % max_points == 0:
            timeout_count = total_points // max_points

            # 時間計算: 基本時間 * (倍率 ^ (通算TO回数 - 1))
            calc_minutes = int(base_minutes * (multiplier ** (timeout_count - 1)))

            # Discordの上限は28日間（40,320分）
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
                await message.channel.send(
                    "❌ タイムアウト権限がないか、対象ユーザーの権限（ロール位置）がBotより上位です。"
                )
            except Exception as e:
                print(f"[Error] タイムアウトエラー: {e}")

    await bot.process_commands(message)


# ==========================================
# 7. スラッシュコマンド機能
# ==========================================


# --- 個別ブロック指定の追加 ---
@bot.tree.command(
    name="block_user",
    description="【管理者専用】免除ロールを持っていても個別にNGワード検知対象にするユーザーを指定します",
)
@has_admin_role()
async def block_user(interaction: discord.Interaction, user: discord.Member):
    config = get_config()
    block_list = [int(uid) for uid in config.get("block_user_ids", [])]

    if user.id in block_list:
        await interaction.response.send_message(
            f"{user.mention} さんは既に個別ブロック対象に登録されています。",
            ephemeral=True,
        )
        return

    block_list.append(user.id)
    config["block_user_ids"] = block_list
    save_json(CONFIG_FILE, config)

    await interaction.response.send_message(
        f"🚫 {user.mention} さんを個別ブロック対象に指定しました。（免除ロールを持っていても検知されます）",
        ephemeral=True,
    )


# --- 個別ブロック指定の解除 ---
@bot.tree.command(
    name="unblock_user",
    description="【管理者専用】ユーザーの個別ブロック指定を解除します",
)
@has_admin_role()
async def unblock_user(interaction: discord.Interaction, user: discord.Member):
    config = get_config()
    block_list = [int(uid) for uid in config.get("block_user_ids", [])]

    if user.id not in block_list:
        await interaction.response.send_message(
            f"{user.mention} さんは個別ブロック対象に登録されていません。",
            ephemeral=True,
        )
        return

    block_list.remove(user.id)
    config["block_user_ids"] = block_list
    save_json(CONFIG_FILE, config)

    await interaction.response.send_message(
        f"✅ {user.mention} さんの個別ブロック指定を解除しました。",
        ephemeral=True,
    )


# --- 【一般用】自分の通算警告回数を確認 ---
@bot.tree.command(
    name="my_points", description="自分の現在の通算（累計）警告回数を確認します"
)
async def my_points(interaction: discord.Interaction):
    user_points = load_json(POINTS_FILE, {})
    user_id = str(interaction.user.id)
    total_points = user_points.get(user_id, 0)

    await interaction.response.send_message(
        f"📊 {interaction.user.mention} さんの通算警告回数は **{total_points} 回** です。",
        ephemeral=True,
    )


# --- 【管理者専用】他ユーザーの通算警告回数を確認 ---
@bot.tree.command(
    name="check_points",
    description="【管理者専用】指定したユーザーの通算（累計）警告回数を確認します",
)
@has_admin_role()
async def check_points(interaction: discord.Interaction, user: discord.Member):
    user_points = load_json(POINTS_FILE, {})
    user_id = str(user.id)
    total_points = user_points.get(user_id, 0)

    await interaction.response.send_message(
        f"🔍 {user.mention} さんの通算警告回数は **{total_points} 回** です。",
        ephemeral=True,
    )


# --- 【管理者専用】警告回数をリセット ---
@bot.tree.command(
    name="reset_points",
    description="【管理者専用】指定したユーザーの通算警告回数を0にリセットします",
)
@has_admin_role()
async def reset_points(interaction: discord.Interaction, user: discord.Member):
    user_points = load_json(POINTS_FILE, {})
    user_id = str(user.id)

    user_points[user_id] = 0
    save_json(POINTS_FILE, user_points)

    await interaction.response.send_message(
        f"🔄 {user.mention} さんの通算警告回数を 0 回にリセットしました。",
        ephemeral=True,
    )


# --- NGワード追加 ---
@bot.tree.command(name="add_ng", description="【管理者専用】NGワードを追加します")
@has_admin_role()
async def add_ng_word(interaction: discord.Interaction, word: str):
    ng_words = load_json(NG_WORDS_FILE, [])

    if word in ng_words:
        await interaction.response.send_message(
            f"「{word}」はすでに登録されています。", ephemeral=True
        )
        return

    ng_words.append(word)
    save_json(NG_WORDS_FILE, ng_words)

    await interaction.response.send_message(
        f"✅ NGワードに「{word}」を追加しました。", ephemeral=True
    )

    if interaction.guild:
        await send_ng_list_update(
            interaction.guild, f"NGワードが追加されました（追加: {word}）"
        )


# --- NGワード削除 ---
@bot.tree.command(name="remove_ng", description="【管理者専用】NGワードを削除します")
@has_admin_role()
async def remove_ng_word(interaction: discord.Interaction, word: str):
    ng_words = load_json(NG_WORDS_FILE, [])

    if word not in ng_words:
        await interaction.response.send_message(
            f"「{word}」は登録されていません。", ephemeral=True
        )
        return

    ng_words.remove(word)
    save_json(NG_WORDS_FILE, ng_words)

    await interaction.response.send_message(
        f"🗑️ NGワードから「{word}」を削除しました。", ephemeral=True
    )

    if interaction.guild:
        await send_ng_list_update(
            interaction.guild, f"NGワードが削除されました（削除: {word}）"
        )


# --- NGワード一覧表示 ---
@bot.tree.command(name="list_ng", description="登録中のNGワード一覧を確認します")
@has_admin_role()
async def list_ng_words(interaction: discord.Interaction):
    ng_words = load_json(NG_WORDS_FILE, [])

    if not ng_words:
        await interaction.response.send_message(
            "現在登録されているNGワードはありません。", ephemeral=True
        )
        return

    word_list = "\n".join([f"・ {w}" for w in ng_words])
    await interaction.response.send_message(
        f"📋 **現在のNGワード一覧:**\n{word_list}", ephemeral=True
    )


# --- 免除ロールの設定 ---
@bot.tree.command(
    name="set_exempt_role",
    description="【管理者専用】NGワードの検知対象外（免除）にする役職を設定します",
)
@has_admin_role()
async def set_exempt_role(
    interaction: discord.Interaction, role: discord.Role
):
    config = get_config()
    config["exempt_role_name"] = role.name
    save_json(CONFIG_FILE, config)

    await interaction.response.send_message(
        f"🛡️ NGワード検知の免除対象役職を **{role.name}** に設定しました。",
        ephemeral=True,
    )


# --- 通知チャンネル設定 ---
@bot.tree.command(
    name="set_channel",
    description="【管理者専用】NGワード更新通知を送信するチャンネルを設定します",
)
@has_admin_role()
async def set_channel(
    interaction: discord.Interaction, channel: discord.TextChannel
):
    config = get_config()
    config["notify_channel_id"] = channel.id
    save_json(CONFIG_FILE, config)

    await interaction.response.send_message(
        f"📢 NGワードの更新通知チャンネルを {channel.mention} に設定しました。",
        ephemeral=True,
    )


# --- 通知のON/OFF切替 ---
@bot.tree.command(
    name="toggle_notify",
    description="【管理者専用】NGワード更新時の自動通知のON/OFFを切り替えます",
)
@has_admin_role()
async def toggle_notify(interaction: discord.Interaction, enable: bool):
    config = get_config()
    config["notify_enabled"] = enable
    save_json(CONFIG_FILE, config)

    status_str = "有効（ON）" if enable else "無効（OFF）"
    await interaction.response.send_message(
        f"⚙️ NGワード更新時の自動通知を **{status_str}** に設定しました。",
        ephemeral=True,
    )


# --- タイムアウト基準・倍率設定 ---
@bot.tree.command(
    name="set_timeout_rules",
    description="【管理者専用】タイムアウトまでの違反間隔、基本時間、重ねがけ倍率を設定します",
)
@has_admin_role()
async def set_timeout_rules(
    interaction: discord.Interaction,
    max_points: int,
    minutes: int,
    multiplier: float = 2.0,
):
    if max_points <= 0 or minutes <= 0 or multiplier < 1.0:
        await interaction.response.send_message(
            "回数・時間は1以上、倍率は1.0以上（2倍にするなら2.0）を指定してください。",
            ephemeral=True,
        )
        return

    config = get_config()
    config["max_points"] = max_points
    config["timeout_minutes"] = minutes
    config["timeout_multiplier"] = multiplier
    save_json(CONFIG_FILE, config)

    mult_text = (
        f"{multiplier}倍（重ねるごとに拡大）" if multiplier > 1.0 else "等倍（毎回固定）"
    )

    await interaction.response.send_message(
        f"⚙️ タイムアウト設定を変更しました：\n"
        f"・ **トリガーサイクル:** {max_points}回ごと（例: {max_points}回, {max_points*2}回...）\n"
        f"・ **初回タイムアウト時間:** {minutes}分間\n"
        f"・ **重ねがけ倍率:** {mult_text}",
        ephemeral=True,
    )


# --- 現在の設定確認 ---
@bot.tree.command(
    name="show_config", description="【管理者専用】現在の各種設定を確認します"
)
@has_admin_role()
async def show_config(interaction: discord.Interaction):
    config = get_config()

    channel_id = config.get("notify_channel_id")
    channel_mention = f"<#{channel_id}>" if channel_id else "未設定"
    notify_status = (
        "ON (有効)" if config.get("notify_enabled", True) else "OFF (無効)"
    )
    exempt_role = config.get("exempt_role_name") or "未設定"

    block_user_ids = config.get("block_user_ids", [])
    if block_user_ids:
        blocked_mentions = " ".join([f"<@{uid}>" for uid in block_user_ids])
    else:
        blocked_mentions = "なし"

    multiplier = config.get("timeout_multiplier", 2.0)
    mult_str = f"{multiplier} 倍" if multiplier > 1.0 else "なし (固定)"

    msg = (
        f"⚙️ **現在の設定状況:**\n"
        f"・ **タイムアウト発生基準:** {config.get('max_points', 3)} 回ごと（3回, 6回, 9回...）\n"
        f"・ **初回タイムアウト時間:** {config.get('timeout_minutes', 5)} 分間\n"
        f"・ **重ねがけ倍率:** {mult_str}\n"
        f"・ **免除対象の役職:** `{exempt_role}`\n"
        f"・ **個別ブロックユーザー:** {blocked_mentions}\n"
        f"・ **通知チャンネル:** {channel_mention}\n"
        f"・ **自動通知機能:** {notify_status}"
    )

    await interaction.response.send_message(msg, ephemeral=True)


# ==========================================
# 8. エラーハンドラー
# ==========================================
@add_ng_word.error
@remove_ng_word.error
@list_ng_words.error
@set_exempt_role.error
@set_channel.error
@toggle_notify.error
@set_timeout_rules.error
@show_config.error
@check_points.error
@reset_points.error
@block_user.error
@unblock_user.error
async def admin_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
):
    if isinstance(error, app_commands.MissingRole):
        await interaction.response.send_message(
            f"❌ このコマンドを実行するには『**{error.missing_role}**』の役職が必要です。",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            "❌ コマンドの実行中にエラーが発生しました。", ephemeral=True
        )


# ==========================================
# 9. プログラムの実行
# ==========================================
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")

    if not TOKEN:
        print("[CRITICAL] 環境変数 'DISCORD_BOT_TOKEN' が設定されていません。")
    else:
        keep_alive()
        bot.run(TOKEN)

import datetime
import json
import os
from threading import Thread
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
ADMIN_ROLE_NAME = "スーパーモデレーター","モデレーター"

# デフォルト設定値
DEFAULT_CONFIG = {
    "max_points": 3,
    "timeout_minutes": 5,
    "notify_channel_id": None,
    "notify_enabled": True,
    "exempt_role_name": "VIP",
}


# ==========================================
# 3. カスタム権限チェック（管理者ロール判定）
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
# 4. JSONファイル操作用のヘルパー関数
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


# ★ 通知送信バグ修正版関数
async def send_ng_list_update(guild: discord.Guild, title_text: str):
    config = get_config()
    if not config.get("notify_enabled", True):
        return

    channel_id = config.get("notify_channel_id")
    if not channel_id:
        return

    # IDをint型にキャストして確実にチャンネルを取得（キャッシュにない場合はfetch）
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
# 5. Botのイベント処理
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

    # 免除ロールまたは管理者ロールを持っているユーザーはスキップ
    if isinstance(message.author, discord.Member):
        exempt_role_name = config.get("exempt_role_name", "VIP")
        user_role_names = [role.name for role in message.author.roles]

        if (
            exempt_role_name in user_role_names
            or ADMIN_ROLE_NAME in user_role_names
        ):
            await bot.process_commands(message)
            return

    ng_words = load_json(NG_WORDS_FILE, [])
    user_points = load_json(POINTS_FILE, {})

    max_points = config.get("max_points", 3)
    timeout_minutes = config.get("timeout_minutes", 5)

    # NGワード判定
    contains_ng_word = any(ng_word in message.content for ng_word in ng_words)

    if contains_ng_word:
        user_id = str(message.author.id)

        current_points = user_points.get(user_id, 0) + 1
        user_points[user_id] = current_points
        save_json(POINTS_FILE, user_points)

        try:
            await message.delete()
        except discord.errors.Forbidden:
            pass
        except discord.errors.HTTPException:
            pass

        await message.channel.send(
            f"{message.author.mention} 不適切なワードを検知しました。（累積: {current_points}/{max_points}回）",
            delete_after=5,
        )

        if current_points >= max_points:
            duration = datetime.timedelta(minutes=timeout_minutes)
            try:
                await message.author.timeout(
                    duration, reason="NGワード規定回数超過"
                )
                await message.channel.send(
                    f"⚠️ {message.author.mention} が規定回数を超えたため、{timeout_minutes}分間タイムアウトされました。"
                )
            except discord.errors.Forbidden:
                await message.channel.send(
                    "❌ タイムアウト権限がないか、対象ユーザーの権限がBotより上位です。"
                )
            except Exception as e:
                print(f"[Error] タイムアウトエラー: {e}")

            user_points[user_id] = 0
            save_json(POINTS_FILE, user_points)

    await bot.process_commands(message)


# ==========================================
# 6. スラッシュコマンド機能
# ==========================================


# --- 【一般用】自分の警告回数を確認 ---
@bot.tree.command(
    name="my_points", description="自分の現在の警告累積回数を確認します"
)
async def my_points(interaction: discord.Interaction):
    user_points = load_json(POINTS_FILE, {})
    config = get_config()

    user_id = str(interaction.user.id)
    current_points = user_points.get(user_id, 0)
    max_points = config.get("max_points", 3)

    await interaction.response.send_message(
        f"📊 {interaction.user.mention} さんの現在の警告累積回数は **{current_points} / {max_points} 回** です。",
        ephemeral=True,  # 実行した本人のみ表示
    )


# --- 【管理者専用】他ユーザーの警告回数を確認 ---
@bot.tree.command(
    name="check_points",
    description="【管理者専用】指定したユーザーの警告累積回数を確認します",
)
@has_admin_role()
async def check_points(interaction: discord.Interaction, user: discord.Member):
    user_points = load_json(POINTS_FILE, {})
    config = get_config()

    user_id = str(user.id)
    current_points = user_points.get(user_id, 0)
    max_points = config.get("max_points", 3)

    await interaction.response.send_message(
        f"🔍 {user.mention} さんの現在の警告累積回数は **{current_points} / {max_points} 回** です。",
        ephemeral=True,
    )


# --- 【管理者専用】警告回数をリセット ---
@bot.tree.command(
    name="reset_points",
    description="【管理者専用】指定したユーザーの警告累積回数を0にリセットします",
)
@has_admin_role()
async def reset_points(interaction: discord.Interaction, user: discord.Member):
    user_points = load_json(POINTS_FILE, {})
    user_id = str(user.id)

    user_points[user_id] = 0
    save_json(POINTS_FILE, user_points)

    await interaction.response.send_message(
        f"🔄 {user.mention} さんの警告累積回数を 0 回にリセットしました。",
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


# --- タイムアウト基準設定 ---
@bot.tree.command(
    name="set_timeout_rules",
    description="【管理者専用】タイムアウトまでの違反回数と禁止時間を設定します",
)
@has_admin_role()
async def set_timeout_rules(
    interaction: discord.Interaction, max_points: int, minutes: int
):
    if max_points <= 0 or minutes <= 0:
        await interaction.response.send_message(
            "回数と時間は1以上の数字を指定してください。", ephemeral=True
        )
        return

    config = get_config()
    config["max_points"] = max_points
    config["timeout_minutes"] = minutes
    save_json(CONFIG_FILE, config)

    await interaction.response.send_message(
        f"⚙️ 設定を変更しました：\n・ **違反回数基準:** {max_points}回\n・ **タイムアウト時間:** {minutes}分間",
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
    exempt_role = config.get("exempt_role_name", "未設定")

    msg = (
        f"⚙️ **現在の設定状況:**\n"
        f"・ **タイムアウト発生回数:** {config.get('max_points', 3)} 回\n"
        f"・ **タイムアウト時間:** {config.get('timeout_minutes', 5)} 分間\n"
        f"・ **免除対象の役職:** `{exempt_role}`\n"
        f"・ **通知チャンネル:** {channel_mention}\n"
        f"・ **自動通知機能:** {notify_status}"
    )

    await interaction.response.send_message(msg, ephemeral=True)


# ==========================================
# 7. エラーハンドラー
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
# 8. プログラムの実行
# ==========================================
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")

    if not TOKEN:
        print("[CRITICAL] 環境変数 'DISCORD_BOT_TOKEN' が設定されていません。")
    else:
        keep_alive()
        bot.run(TOKEN)

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

# ★ NGワード編集・設定変更を許可するロール名
ADMIN_ROLE_NAME = "モデレーター"

# デフォルト設定値（config.json がない場合に使用）
DEFAULT_CONFIG = {
    "max_points": 3,  # 何回でタイムアウトか
    "timeout_minutes": 5,  # 何分間タイムアウトか
    "notify_channel_id": None,  # 通知先チャンネルID
    "notify_enabled": True,  # 通知機能のON/OFF
}


# ==========================================
# 3. カスタム権限チェック（ロール判定）
# ==========================================
def has_admin_role():
    """実行ユーザーが指定のロール名を持っているかチェックする"""

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


# 設定を取得する便利関数
def get_config():
    config = load_json(CONFIG_FILE, DEFAULT_CONFIG)
    # 不足しているキーがあればデフォルトで補う
    for key, value in DEFAULT_CONFIG.items():
        if key not in config:
            config[key] = value
    return config


# 通知メッセージの公開送信
async def send_ng_list_update(guild: discord.Guild, title_text: str):
    config = get_config()
    if not config.get("notify_enabled", True):
        return

    channel_id = config.get("notify_channel_id")
    if not channel_id:
        return

    channel = guild.get_channel(channel_id)
    if channel:
        ng_words = load_json(NG_WORDS_FILE, [])
        if ng_words:
            word_list = "\n".join([f"・ {w}" for w in ng_words])
        else:
            word_list = "（現在登録されているNGワードはありません）"

        embed = discord.Embed(
            title=f"📢 {title_text}",
            description=f"**現在のNGワード一覧:**\n{word_list}",
            color=discord.Color.blue(),
        )
        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"[Error] 通知の送信に失敗しました: {e}")


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

    ng_words = load_json(NG_WORDS_FILE, [])
    user_points = load_json(POINTS_FILE, {})
    config = get_config()

    max_points = config.get("max_points", 3)
    timeout_minutes = config.get("timeout_minutes", 5)

    # NGワード判定（部分一致）
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
# 6. スラッシュコマンド機能（指定ロール限定）
# ==========================================


# --- NGワード追加 ---
@bot.tree.command(
    name="add_ng", description="【指定ロール専用】NGワードを追加します"
)
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

    # チャンネルへ更新通知を自動送信（公開）
    if interaction.guild:
        await send_ng_list_update(
            interaction.guild, f"NGワードが追加されました（追加: {word}）"
        )


# --- NGワード削除 ---
@bot.tree.command(
    name="remove_ng", description="【指定ロール専用】NGワードを削除します"
)
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

    # チャンネルへ更新通知を自動送信（公開）
    if interaction.guild:
        await send_ng_list_update(
            interaction.guild, f"NGワードが削除されました（削除: {word}）"
        )


# --- NGワード一覧表示 ---
@bot.tree.command(
    name="list_ng", description="登録中のNGワード一覧を確認します"
)
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


# --- 通知チャンネル設定 ---
@bot.tree.command(
    name="set_channel",
    description="【指定ロール専用】NGワード更新通知を送信するチャンネルを設定します",
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
    description="【指定ロール専用】NGワード更新時の自動通知のON/OFFを切り替えます",
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
    description="【指定ロール専用】タイムアウトまでの違反回数と禁止時間を設定します",
)
@has_admin_role()
@app_commands.describe(
    max_points="タイムアウトになる違反回数 (例: 3)",
    minutes="タイムアウトにする時間/分 (例: 5)",
)
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
    name="show_config",
    description="【指定ロール専用】現在の通知・タイムアウト設定を確認します",
)
@has_admin_role()
async def show_config(interaction: discord.Interaction):
    config = get_config()

    channel_id = config.get("notify_channel_id")
    channel_mention = f"<#{channel_id}>" if channel_id else "未設定"
    notify_status = (
        "ON (有効)" if config.get("notify_enabled", True) else "OFF (無効)"
    )

    msg = (
        f"⚙️ **現在の設定状況:**\n"
        f"・ **タイムアウト発生回数:** {config.get('max_points', 3)} 回\n"
        f"・ **タイムアウト時間:** {config.get('timeout_minutes', 5)} 分間\n"
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
@set_channel.error
@toggle_notify.error
@set_timeout_rules.error
@show_config.error
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

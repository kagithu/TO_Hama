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

MAX_POINTS = 3  # タイムアウトになる違反回数
TIMEOUT_MINUTES = 5  # タイムアウト時間（分）
NG_WORDS_FILE = "ng_words.json"
POINTS_FILE = "user_points.json"

# ★ NGワード編集を許可するロール名（Discord上の役職名と完全に一致させてください）
ADMIN_ROLE_NAME = "スーパーモデレーター"


# ==========================================
# 3. ロール名チェック用の関数（カスタムチェック）
# ==========================================
def has_admin_role():
    """実行ユーザーが指定のロール名を持っているかチェックする"""

    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False

        # ユーザーが持っているロールの中に ADMIN_ROLE_NAME があるか判定
        user_role_names = [role.name for role in interaction.user.roles]

        # サーバーオーナー（所有者）はロールに関わらず常に許可したい場合は以下のコメントアウトを解除
        # if interaction.guild and interaction.user.id == interaction.guild.owner_id:
        #     return True

        if ADMIN_ROLE_NAME in user_role_names:
            return True

        # ロールを持っていない場合は権限エラーを発生させる
        raise app_commands.MissingRole(ADMIN_ROLE_NAME)

    return app_commands.check(predicate)


# ==========================================
# 4. JSONファイル操作用の関数
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


# ==========================================
# 5. Botのイベント処理
# ==========================================
@bot.event
async def on_ready():
    # スラッシュコマンドをDiscordと同期
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
            f"{message.author.mention} 不適切なワードを検知しました。（累積: {current_points}/{MAX_POINTS}回）",
            delete_after=5,
        )

        if current_points >= MAX_POINTS:
            duration = datetime.timedelta(minutes=TIMEOUT_MINUTES)
            try:
                await message.author.timeout(
                    duration, reason="NGワード規定回数超過"
                )
                await message.channel.send(
                    f"⚠️ {message.author.mention} が規定回数を超えたため、{TIMEOUT_MINUTES}分間タイムアウトされました。"
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


# --- NGワード追加コマンド ---
@bot.tree.command(
    name="add_ng", description="【指定ロール専用】NGワードを追加します"
)
@has_admin_role()  # 指定したロール名を持っているかチェック
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


# --- NGワード削除コマンド ---
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


# --- NGワード一覧表示コマンド ---
@bot.tree.command(
    name="list_ng",
    description="【指定ロール専用】登録中のNGワード一覧を確認します",
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
        f"📋 **現在のNGワード一覧:**\n{word_list}",
        ephemeral=True,  # 実行したユーザーにのみ表示
    )


# ==========================================
# 7. エラーハンドラー（ロールがない場合の処理）
# ==========================================
@add_ng_word.error
@remove_ng_word.error
@list_ng_words.error
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

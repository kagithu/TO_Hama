import datetime
import json
import os
from threading import Thread
import discord
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
    # Renderが割り当てるポート番号（指定がなければ10000）を取得して起動
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    """Webサーバーをバックグラウンドで起動する関数"""
    t = Thread(target=run_flask)
    t.start()


# ==========================================
# 2. Discord Botの設定
# ==========================================
intents = discord.Intents.default()
intents.message_content = True  # Message Content Intent を有効化

bot = commands.Bot(command_prefix="!", intents=intents)

MAX_POINTS = 3  # タイムアウトになる違反回数
TIMEOUT_MINUTES = 5  # タイムアウト時間（分）
NG_WORDS_FILE = "ng_words.json"
POINTS_FILE = "user_points.json"


# ==========================================
# 3. JSONファイル操作用の関数
# ==========================================
def load_json(filepath, default_value):
    """JSONファイルを読み込む（存在しなければ作成）"""
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
    """JSONファイルにデータを書き込む"""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"[Error] JSON保存失敗 ({filepath}): {e}")


# ==========================================
# 4. Botのイベント処理
# ==========================================
@bot.event
async def on_ready():
    print("----------------------------------------")
    print(f"Logged in as: {bot.user.name} (ID: {bot.user.id})")
    print("Bot is ready and listening!")
    print("----------------------------------------")


@bot.event
async def on_message(message):
    # Bot自身のメッセージは無視
    if message.author.bot:
        return

    # 最新のNGワードとユーザーポイントを取得
    ng_words = load_json(
        NG_WORDS_FILE, ["サンプル1", "サンプル2"]
    )  # デフォルトNGワード
    user_points = load_json(POINTS_FILE, {})

    # メッセージ内にNGワードが含まれているか判定（部分一致）
    contains_ng_word = any(ng_word in message.content for ng_word in ng_words)

    if contains_ng_word:
        user_id = str(message.author.id)

        # ポイント計算と保存
        current_points = user_points.get(user_id, 0) + 1
        user_points[user_id] = current_points
        save_json(POINTS_FILE, user_points)

        # 違反メッセージの削除を試行
        try:
            await message.delete()
        except discord.errors.Forbidden:
            print(
                "[Warning] メッセージ削除権限（Manage Messages）がありません。"
            )
        except discord.errors.HTTPException:
            pass

        # チャンネルへ警告メッセージを送信（5秒後に自動消去）
        await message.channel.send(
            f"{message.author.mention} 不適切なワードを検知しました。（累積: {current_points}/{MAX_POINTS}回）",
            delete_after=5,
        )

        # 規定ポイント（3回）に達した場合のタイムアウト処理
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
                    "❌ タイムアウトに失敗しました。Botに『メンバーのタイムアウト』権限がないか、対象ユーザーの権限がBotより上位です。"
                )
            except Exception as e:
                print(f"[Error] タイムアウト実行時エラー: {e}")

            # カウントをリセットして保存
            user_points[user_id] = 0
            save_json(POINTS_FILE, user_points)

    # コマンド機能も使用できるようにする
    await bot.process_commands(message)


# ==========================================
# 5. プログラムの実行
# ==========================================
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")

    if not TOKEN:
        print(
            "[CRITICAL] 環境変数 'DISCORD_BOT_TOKEN' が設定されていません。"
        )
        print("Renderの Environment 画面でトークンを設定してください。")
    else:
        # Webサーバーをバックグラウンドで起動
        keep_alive()
        # Discord Botを起動
        bot.run(TOKEN)

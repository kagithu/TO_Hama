import datetime
import json
import os
import discord
from discord.ext import commands

# 1. インテントの設定
intents = discord.Intents.default()
intents.message_content = True  # Message Content Intent を有効化

bot = commands.Bot(command_prefix="!", intents=intents)

# 2. 設定値
MAX_POINTS = 3
TIMEOUT_MINUTES = 5
NG_WORDS_FILE = "ng_words.json"
POINTS_FILE = "user_points.json"


# 3. JSON操作ヘルパー関数
def load_json(filepath, default_value):
    """JSONファイルを読み込む（存在しない場合は作成）"""
    if not os.path.exists(filepath):
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(default_value, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[Error] JSONの作成に失敗しました ({filepath}): {e}")
        return default_value

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Error] JSONの読み込みに失敗しました ({filepath}): {e}")
        return default_value


def save_json(filepath, data):
    """JSONファイルにデータを書き込む"""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"[Error] JSONの保存に失敗しました ({filepath}): {e}")


# 4. イベントハンドラー
@bot.event
async def on_ready():
    print("----------------------------------------")
    print(f"Logged in as: {bot.user.name} (ID: {bot.user.id})")
    print("Bot is ready on Koyeb!")
    print("----------------------------------------")


@bot.event
async def on_message(message):
    # Bot自身のメッセージは無視
    if message.author.bot:
        return

    # 最新のNGワードとポイントを読み込み
    ng_words = load_json(
        NG_WORDS_FILE, ["サンプルNG1", "サンプルNG2"]
    )
    user_points = load_json(POINTS_FILE, {})

    # メッセージ内にNGワードが含まれているか判定
    contains_ng_word = any(ng_word in message.content for ng_word in ng_words)

    if contains_ng_word:
        user_id = str(message.author.id)

        # ポイント計算・保存
        current_points = user_points.get(user_id, 0) + 1
        user_points[user_id] = current_points
        save_json(POINTS_FILE, user_points)

        # 違反メッセージの削除（権限があれば実行）
        try:
            await message.delete()
        except discord.errors.Forbidden:
            print(
                "[Warning] メッセージ削除権限（Manage Messages）がありません。"
            )
        except discord.errors.HTTPException:
            pass

        # 警告メッセージ送信（5秒後に自動削除）
        await message.channel.send(
            f"{message.author.mention} 不適切なワードを検知しました。（累積: {current_points}/{MAX_POINTS}回）",
            delete_after=5,
        )

        # タイムアウト処理
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
                    "❌ タイムアウト権限（Moderate Members）が無いか、対象ユーザーの権限がBotより高いためタイムアウトできませんでした。"
                )
            except Exception as e:
                print(f"[Error] タイムアウト実行時にエラーが発生しました: {e}")

            # カウントリセット
            user_points[user_id] = 0
            save_json(POINTS_FILE, user_points)

    # コマンド実行を有効にする
    await bot.process_commands(message)


# 5. Koyebの環境変数からトークンを取得して起動
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")

    if not TOKEN:
        print(
            "[CRITICAL] 環境変数 'DISCORD_BOT_TOKEN' が設定されていません。"
        )
        print("Koyebのダッシュボードで Environment Variables を設定してください。")
    else:
        bot.run(TOKEN)
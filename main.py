# -*- coding: utf-8 -*-
import os
import sys
import re
import asyncio
import time

# Сторонние либы
from PIL import Image, ImageDraw, ImageFont
from telethon import TelegramClient
import vk_api
from dotenv import load_dotenv

# Подгружаем конфиг
load_dotenv()

# --- Конфигурация ---
TG_API_ID = int(os.getenv("TG_API_ID"))
TG_API_HASH = os.getenv("TG_API_HASH")
CHANNEL_SRC = os.getenv("TG_CHANNEL_USERNAME")
LOG_CHANNEL_ID = int(os.getenv("TG_LOG_CHANNEL_ID"))
VK_TOKEN = os.getenv("VK_TOKEN")
VK_GROUP = int(os.getenv("VK_GROUP_ID"))
START_ID = int(os.getenv("START_FROM_ID", 0))

# --- ⛔️ ФИЛЬТР (СТОП-СЛОВА) ---
STOP_WORDS = [
    "расписание учебных занятий",
    "расписание занятий",
    "изменения в расписании",
    "расписание на",
    "замена занятий"
]

# Инициализация клиентов
vk_session = vk_api.VkApi(token=VK_TOKEN)
vk = vk_session.get_api()
uploader = vk_api.VkUpload(vk_session)
client = TelegramClient('anon_session', TG_API_ID, TG_API_HASH)

# ---------------- ФУНКЦИИ ----------------

def clear_trash():
    """Чистит папку от временных файлов"""
    trash_ext = ['.jpg', '.jpeg', '.png', '.webp', '.mp4', '.mov', '.gif']
    count = 0
    files = os.listdir('.')
    for f in files:
        if any(f.lower().endswith(ext) for ext in trash_ext):
            try:
                os.remove(f)
                count += 1
            except: pass
    if count > 0: print(f"🧹 Уборщик: удалил {count} файлов.")

def fix_markdown(text):
    if not text: return ""
    text = re.sub(r'\*\*+(.+?)\*\*+', r'\1', text, flags=re.S) 
    text = re.sub(r'__+(.+?)__+', r'\1', text, flags=re.S)
    text = re.sub(r'~~+(.+?)~~+', r'\1', text, flags=re.S)
    text = re.sub(r'`+(.+?)`+', r'\1', text, flags=re.S)
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'\1 (\2)', text)
    for char in ['**', '__', '~~', '`']:
        text = text.replace(char, '')
    return text.strip()

def make_video_stub(msg_id):
    fname = f"video_stub_{msg_id}.jpg"
    w, h = 1280, 720
    img = Image.new('RGB', (w, h), color='black')
    draw = ImageDraw.Draw(img)
    txt = "ВИДЕО ДОСТУПНО\nПО ССЫЛКЕ В ПОСТЕ\nV  V  V"
    
    font = None
    possible_fonts = ["arial.ttf", "Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    for f_name in possible_fonts:
        try:
            font = ImageFont.truetype(f_name, 70)
            break
        except: continue
    if font is None: font = ImageFont.load_default()

    draw.text((w/2, h/2), txt, fill="white", font=font, anchor="mm", align="center")
    img.save(fname)
    return fname

def upload_vk_photo(paths):
    if not paths: return []
    real_paths = [p for p in paths if p]
    uploaded_tags = []
    for p in real_paths:
        try:
            if os.path.getsize(p) == 0:
                continue
            up = uploader.photo_wall(p)
            att = f"photo{up[0]['owner_id']}_{up[0]['id']}"
            uploaded_tags.append(att)
        except Exception as e:
            print(f"❌ Ошибка загрузки {p}: {e}")
            continue
    return uploaded_tags

# --- БАЗА ДАННЫХ (в ТГ канале) ---

async def get_last_id():
    try:
        msgs = await client.get_messages(LOG_CHANNEL_ID, limit=1)
        if msgs:
            text_content = msgs[0].text or ""
            found = re.search(r'(\d+)', text_content)
            if found: return int(found.group(1))
    except Exception as e: print(f"Ошибка чтения базы: {e}")
    return 0

async def set_last_id(new_id):
    try: await client.send_message(LOG_CHANNEL_ID, f"Last Processed ID: {new_id}")
    except: print(f"Не смог сохранить ID {new_id} в лог!")

# ---------------- MAIN ----------------

async def run_bot():
    print("--------------------------------")
    print("🚀 Бот запускается (v16.0 No Author)...")
    print("--------------------------------")
    
    async with client:
        last_id = await get_last_id()
        
        if last_id == 0:
            if START_ID > 0:
                print(f"⚠️ База пуста, начинаем с ID из конфига: {START_ID}")
                last_id = START_ID
            else:
                print("ℹ️ База пуста, начинаем с нуля.")
        else:
            print(f"✅ Последний обработанный пост: {last_id}")

        raw_msgs = await client.get_messages(CHANNEL_SRC, limit=100)
        raw_msgs = sorted(raw_msgs, key=lambda x: x.id)

        groups = []
        buffer = []
        for m in raw_msgs:
            if m.id <= last_id or m.action: continue
            if m.grouped_id:
                if not buffer or buffer[0].grouped_id != m.grouped_id:
                    if buffer: groups.append(buffer)
                    buffer = [m]
                else: buffer.append(m)
            else:
                if buffer:
                    groups.append(buffer)
                    buffer = []
                groups.append([m])
        if buffer: groups.append(buffer)

        if not groups:
            print("💤 Ничего нового нет.")
            return

        for batch in groups:
            top_msg = batch[-1]
            print(f"\n🆕 Обработка поста ID {top_msg.id}...")

            txt = ""
            for x in batch:
                if x.text:
                    txt = x.text
                    break
            
            clean_txt = fix_markdown(txt)
            
            # --- ФИЛЬТР ---
            low_txt = clean_txt.lower()
            is_spam = False
            for word in STOP_WORDS:
                if word in low_txt:
                    is_spam = True
                    break
            
            if is_spam:
                print(f"   🚫 Скипаем пост (Стоп-слово: '{word}')")
                await set_last_id(top_msg.id)
                continue
            # --------------

            vid_links = []
            for x in batch:
                if x.video:
                    link = f"https://t.me/{CHANNEL_SRC}/{x.id}"
                    vid_links.append(link)

            if vid_links:
                clean_txt += "\n\n🎥 Видео доступно по ссылке:"
                for l in vid_links:
                    clean_txt += f"\n▶️ {l}"

            tasks = []
            local_files = [] 
            
            for x in batch:
                if x.photo:
                    tasks.append(client.download_media(x))
                elif x.video:
                    stub = make_video_stub(x.id)
                    local_files.append(stub)

            dl_files = []
            if tasks:
                print(f"   📥 Скачиваю {len(tasks)} фото...")
                dl_files = await asyncio.gather(*tasks)
                dl_files = [f for f in dl_files if f]

            total_files = dl_files + local_files
            vk_atts = []
            
            if total_files:
                print("   📤 Загрузка оригиналов в ВК...")
                vk_atts = upload_vk_photo(total_files)
                for f in total_files:
                    try: 
                        if f and os.path.exists(f): os.remove(f)
                    except: pass

            if not clean_txt and not vk_atts:
                print("   ⚠️ Пост пустой, скипаем.")
                await set_last_id(top_msg.id)
                continue

            try:
                vk.wall.post(
                    owner_id=-VK_GROUP,
                    from_group=1,
                    message=clean_txt,
                    attachments=','.join(vk_atts),
                    # ВОТ ЭТА МАГИЧЕСКАЯ НАСТРОЙКА:
                    signed=0  # 0 = Без подписи (Анонимно от группы)
                )
                print("   ✅ Готово!")
                await set_last_id(top_msg.id)
                time.sleep(3)
                
            except Exception as e:
                print(f"   🔥 Ошибка публикации: {e}")

    print("\n🏁 Всё сделано.")

if __name__ == '__main__':
    try:
        clear_trash()
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\n⛔ Стоп кран.")
    except Exception as e:
        print(f"\n💀 Упало с ошибкой: {e}")
    finally:
        clear_trash()

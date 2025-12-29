# -*- coding: utf-8 -*-
import os
import sys
import re
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

# Сторонние либы
from PIL import Image, ImageDraw, ImageFont
from telethon import TelegramClient
import vk_api
from dotenv import load_dotenv

# Подгружаем конфиг
load_dotenv()

# --- Конфигурация ---
# ID приложения телеграм
TG_API_ID = int(os.getenv("TG_API_ID"))
TG_API_HASH = os.getenv("TG_API_HASH")

# Откуда берем и куда кладем логи
CHANNEL_SRC = os.getenv("TG_CHANNEL_USERNAME")
LOG_CHANNEL_ID = int(os.getenv("TG_LOG_CHANNEL_ID"))

# Настройки ВК
VK_TOKEN = os.getenv("VK_TOKEN")
VK_GROUP = int(os.getenv("VK_GROUP_ID"))

# Если база пустая, начнем с этого ID
START_ID = int(os.getenv("START_FROM_ID", 0))

# Инициализация клиентов
# ВК синхронный, так что пусть висит глобально
vk_session = vk_api.VkApi(token=VK_TOKEN)
vk = vk_session.get_api()
uploader = vk_api.VkUpload(vk_session)

# Телетон клиент
client = TelegramClient('anon_session', TG_API_ID, TG_API_HASH)

# ---------------- ФУНКЦИИ ----------------

def clear_trash():
    """Чистит папку от временных файлов, а то забивается быстро"""
    trash_ext = ['.jpg', '.jpeg', '.png', '.webp', '.mp4', '.mov', '.gif']
    count = 0
    files = os.listdir('.')
    for f in files:
        # Проверяем расширение
        if any(f.lower().endswith(ext) for ext in trash_ext):
            try:
                os.remove(f)
                count += 1
            except:
                pass # Если файл занят, фиг с ним
    
    if count > 0:
        print(f"🧹 Уборщик: удалил {count} файлов.")

def fix_markdown(text):
    if not text: 
        return ""
    
    # Регулярки это боль, но вроде работает
    # Убираем жирный, курсив и прочее форматирование ТГ
    text = re.sub(r'\*\*+(.+?)\*\*+', r'\1', text, flags=re.S) 
    text = re.sub(r'__+(.+?)__+', r'\1', text, flags=re.S)
    text = re.sub(r'~~+(.+?)~~+', r'\1', text, flags=re.S)
    text = re.sub(r'`+(.+?)`+', r'\1', text, flags=re.S)
    # Ссылки делаем читаемыми: [Текст](url) -> Текст (url)
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'\1 (\2)', text)
    
    # На всякий случай чистим мусор, если регулярка пропустила одиночные символы
    for char in ['**', '__', '~~', '`']:
        text = text.replace(char, '')
        
    return text.strip()

def make_video_stub(msg_id):
    # Делаем черную заглушку, чтобы не грузить тяжелое видео
    fname = f"video_stub_{msg_id}.jpg"
    w, h = 1280, 720
    
    img = Image.new('RGB', (w, h), color='black')
    draw = ImageDraw.Draw(img)
    
    # Текст по центру. Смайлики убрал, шрифт их не жрет :(
    txt = "ВИДЕО ДОСТУПНО\nПО ССЫЛКЕ В ПОСТЕ\nV  V  V"
    
    # Пытаемся найти нормальный шрифт
    # На винде Arial, на линуксе (сервере) придется поискать
    font = None
    possible_fonts = [
        "arial.ttf", 
        "Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "DejaVuSans-Bold.ttf"
    ]
    
    for f_name in possible_fonts:
        try:
            font = ImageFont.truetype(f_name, 70)
            break
        except:
            continue
            
    if font is None:
        # Ну если совсем ничего нет, берем дефолтный (мелкий, но хоть что-то)
        font = ImageFont.load_default()

    # Рисуем по центру
    draw.text((w/2, h/2), txt, fill="white", font=font, anchor="mm", align="center")
    
    img.save(fname)
    return fname

def to_webp(path):
    # Конвертация в webp для скорости загрузки
    if not path: return None
    try:
        if path.lower().endswith('.webp'): 
            return path
            
        img = Image.open(path)
        new_name = os.path.splitext(path)[0] + ".webp"
        img.save(new_name, "WEBP", quality=90)
        img.close()
        
        # Старый файл сносим
        os.remove(path)
        return new_name
    except Exception as e:
        # print(f"Не смог конвертировать: {e}") 
        return path # Возвращаем оригинал если не вышло

def upload_vk_photo(paths):
    # Грузим пачкой
    if not paths: return []
    # Фильтруем пустые
    real_paths = [p for p in paths if p]
    
    uploaded_tags = []
    try:
        for p in real_paths:
            # Загрузка на сервер
            up = uploader.photo_wall(p)
            # Формируем ID аттачмента photoXXXX_YYYY
            att = f"photo{up[0]['owner_id']}_{up[0]['id']}"
            uploaded_tags.append(att)
    except Exception as e:
        print(f"❌ Ошибка ВК при загрузке фото: {e}")
        
    return uploaded_tags

# --- БАЗА ДАННЫХ (в ТГ канале) ---

async def get_last_id():
    try:
        # Читаем последнее сообщение из лог-канала
        msgs = await client.get_messages(LOG_CHANNEL_ID, limit=1)
        if msgs:
            # Ищем цифры в тексте
            found = re.search(r'(\d+)', msgs[0].text)
            if found:
                return int(found.group(1))
    except Exception as e:
        print(f"Ошибка чтения базы: {e}")
    return 0

async def set_last_id(new_id):
    try:
        await client.send_message(LOG_CHANNEL_ID, f"Last Processed ID: {new_id}")
    except:
        print(f"Не смог сохранить ID {new_id} в лог!")

# ---------------- MAIN ----------------

async def run_bot():
    print("--------------------------------")
    print("🚀 Бот запускается...")
    print("--------------------------------")
    
    # Тредпул для обработки картинок (чтобы не тормозил основной поток)
    pool = ThreadPoolExecutor(max_workers=4)

    # ВАЖНО: Сначала коннект, потом логика
    async with client:
        # 1. Чекаем последний ID
        last_id = await get_last_id()
        
        # Если база пустая, смотрим настройки
        if last_id == 0:
            if START_ID > 0:
                print(f"⚠️ База пуста, начинаем с ID из конфига: {START_ID}")
                last_id = START_ID
            else:
                print("ℹ️ База пуста, начинаем с нуля.")
        else:
            print(f"✅ Последний обработанный пост: {last_id}")

        # 2. Парсим канал
        # Берем 100 постов с запасом
        raw_msgs = await client.get_messages(CHANNEL_SRC, limit=100)
        # Сортируем от старых к новым
        raw_msgs = sorted(raw_msgs, key=lambda x: x.id)

        # 3. Группируем альбомы
        groups = []
        buffer = []
        
        for m in raw_msgs:
            # Пропускаем старое и служебные сообщения
            if m.id <= last_id or m.action:
                continue

            if m.grouped_id:
                # Если это часть альбома
                if not buffer or buffer[0].grouped_id != m.grouped_id:
                    if buffer: groups.append(buffer)
                    buffer = [m]
                else:
                    buffer.append(m)
            else:
                # Если одиночный пост, сбрасываем буфер
                if buffer:
                    groups.append(buffer)
                    buffer = []
                groups.append([m])
        
        # Докидываем хвост
        if buffer: groups.append(buffer)

        if not groups:
            print("💤 Ничего нового нет.")
            return

        # 4. Погнали обрабатывать
        for batch in groups:
            # Берем последнее сообщение в группе как "главное" для ID
            top_msg = batch[-1]
            print(f"\n🆕 Обработка поста ID {top_msg.id}...")

            # --- Текст ---
            # Ищем текст в любом сообщении из пачки
            txt = ""
            for x in batch:
                if x.text:
                    txt = x.text
                    break
            
            clean_txt = fix_markdown(txt)

            # --- Видео ---
            vid_links = []
            for x in batch:
                if x.video:
                    link = f"https://t.me/{CHANNEL_SRC}/{x.id}"
                    vid_links.append(link)

            # Если есть видео, добавляем ссылки в конец текста
            if vid_links:
                clean_txt += "\n\n🎥 Видео доступно по ссылке:"
                for l in vid_links:
                    clean_txt += f"\n▶️ {l}"

            # --- Медиа (Скачивание) ---
            tasks = []
            local_files = [] # Сюда кладем пути к файлам (и заглушкам)
            
            for x in batch:
                if x.photo:
                    # Фото качаем
                    tasks.append(client.download_media(x))
                elif x.video:
                    # Для видео генерим заглушку
                    stub = make_video_stub(x.id)
                    local_files.append(stub)

            dl_files = []
            if tasks:
                print(f"   📥 Скачиваю {len(tasks)} фото...")
                # Ждем пока скачается
                dl_files = await asyncio.gather(*tasks)
                # Чистим None если что-то не скачалось
                dl_files = [f for f in dl_files if f]

            # Собираем всё в кучу: скачанные фото + заглушки
            total_files = dl_files + local_files
            
            vk_atts = []
            if total_files:
                print("   ⚙️ Конвертация и загрузка в ВК...")
                loop = asyncio.get_running_loop()
                
                # Запускаем конвертацию в тредах
                convert_jobs = [
                    loop.run_in_executor(pool, to_webp, f) 
                    for f in total_files
                ]
                webp_files = await asyncio.gather(*convert_jobs)
                
                # Грузим в ВК
                vk_atts = await upload_vk_photo(webp_files)
                
                # Удаляем временные webp
                for f in webp_files:
                    try:
                        if f and os.path.exists(f): os.remove(f)
                    except: pass

            # Проверка на пустоту
            if not clean_txt and not vk_atts:
                print("   ⚠️ Пост пустой, скипаем, но ID запомним.")
                await set_last_id(top_msg.id)
                continue

            # --- Публикация ---
            try:
                vk.wall.post(
                    owner_id=-VK_GROUP,
                    from_group=1,
                    message=clean_txt,
                    attachments=','.join(vk_atts)
                )
                print("   ✅ Готово!")
                
                # Сохраняем ID, чтобы не повторяться
                await set_last_id(top_msg.id)
                
                # Спим немного, чтобы ВК не банил
                time.sleep(3)
                
            except Exception as e:
                print(f"   🔥 Ошибка публикации: {e}")

    print("\n🏁 Всё сделано.")

if __name__ == '__main__':
    try:
        # Сначала уберемся
        clear_trash()
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\n⛔ Стоп кран.")
    except Exception as e:
        print(f"\n💀 Упало с ошибкой: {e}")
    finally:
        # Убираемся за собой
        clear_trash()
import requests
from bs4 import BeautifulSoup
import json
import os
import re
from datetime import datetime

def parse_telegram_channel(url, pages_to_fetch=3):
    current_url = url
    all_posts = []

    for _ in range(pages_to_fetch):
        response = requests.get(current_url)
        if response.status_code != 200:
            print(f"Failed to fetch {current_url}: Status {response.status_code}")
            break

        soup = BeautifulSoup(response.text, 'html.parser')
        messages = soup.find_all('div', class_='tgme_widget_message_wrap')
        page_posts = []

        for msg in messages:
            text_node = msg.find('div', class_='tgme_widget_message_text')
            if not text_node:
                continue

            # Extract raw HTML and convert <br> to \n
            raw_html = str(text_node)
            clean_text = re.sub(r'<br\s*/?>', '\n', raw_html, flags=re.IGNORECASE)
            clean_text = re.sub(r'<[^>]*>', '', clean_text).strip()

            if not clean_text:
                continue

            # Photo
            photo_node = msg.find('a', class_='tgme_widget_message_photo_wrap')
            image_url = None
            if photo_node and photo_node.has_attr('style'):
                style = photo_node['style']
                match = re.search(r"background-image:url\('([^']+)'\)", style)
                if match:
                    image_url = match.group(1)

            # Date and link
            date_node = msg.find('a', class_='tgme_widget_message_date')
            link = 'https://t.me/pgatkk'
            date_str = datetime.now().strftime('%d.%m.%Y')
            
            if date_node:
                if date_node.has_attr('href'):
                    link = date_node['href']
                time_node = date_node.find('time')
                if time_node and time_node.has_attr('datetime'):
                    try:
                        # e.g., 2024-05-18T10:00:00+00:00
                        dt = datetime.fromisoformat(time_node['datetime'].replace('Z', '+00:00'))
                        date_str = dt.strftime('%d.%m.%Y')
                    except Exception:
                        pass

            # Title and summary
            title = 'Новости колледжа'
            summary = clean_text

            first_line_break = clean_text.find('\n')
            first_dot = clean_text.find('.')

            title_end = -1
            if 0 < first_line_break < 80:
                title_end = first_line_break
            elif 0 < first_dot < 80:
                title_end = first_dot + 1
            else:
                title_end = min(len(clean_text), 60)

            if title_end > 0:
                title = clean_text[:title_end].strip()
                summary = clean_text[title_end:].strip()
                if not summary:
                    summary = clean_text

            # ID
            id_match = re.search(r'/(\d+)$', link)
            post_id = id_match.group(1) if id_match else str(datetime.now().timestamp())

            page_posts.append({
                'id': post_id,
                'title': title,
                'summary': summary,
                'imageUrl': image_url,
                'date': date_str,
                'category': 'Telegram',
                'link': link
            })

        all_posts.extend(reversed(page_posts))

        more_link = soup.find('a', class_='tme_messages_more')
        if more_link and more_link.has_attr('href'):
            next_path = more_link['href']
            current_url = f"https://t.me{next_path}"
        else:
            break

    # all_posts are from newest to oldest page, and inside page they are reversed (newest to oldest)
    # Actually wait. Telegram pages go from oldest (top) to newest (bottom) on a single page?
    # Actually the TS code did: pagePosts.reverse(). Then pushed.
    # Let's just sort them by ID descending to be safe.
    valid_posts = [p for p in all_posts if len(p['title']) > 0]
    
    # Sort by ID as integer descending
    try:
        valid_posts.sort(key=lambda x: int(x['id']), reverse=True)
    except:
        pass # Fallback if ID is not integer

    return valid_posts

if __name__ == '__main__':
    channel_url = 'https://t.me/s/pgatkk'
    print(f"Fetching posts from {channel_url}...")
    posts = parse_telegram_channel(channel_url, pages_to_fetch=3)
    
    # Ensure public/data directory exists
    os.makedirs('public/data', exist_ok=True)
    
    output_file = 'public/data/telegram_news.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)
    
    print(f"Successfully saved {len(posts)} posts to {output_file}")

import os
import re
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import time
from pathlib import Path

def download_images(url):
    """
    使用 Playwright 从给定的 URL 下载商品的主图和详情图。
    该函数针对网商园 (wsy.com) 的页面结构进行了优化。

    Args:
        url (str): 要爬取的商品页面的完整 URL。
    """
    try:
        with sync_playwright() as p:
            headless = os.getenv("WSY_HEADLESS", "").strip() == "1"
            browser = p.chromium.launch(headless=headless)
            state_path = Path("wsy_state.json")
            if state_path.exists():
                context = browser.new_context(storage_state=str(state_path))
            else:
                context = browser.new_context()
            page = context.new_page()
            
            print(f"正在访问页面: {url}")
            page.goto(url, wait_until='load')
            
            # 给予用户足够的时间进行手动登录
            print("\n-----------------------------------------------------------------")
            print("如果浏览器弹出登录页面，请在 2 分钟内手动完成登录。")
            print("登录成功后，脚本将自动继续执行...")
            print("-----------------------------------------------------------------\n")

            # 等待关键元素出现，这表明已成功进入商品页面
            try:
                # 等待我们真正需要的、带有 data-haszoom='1' 属性的图片出现
                page.wait_for_selector('img[data-haszoom="1"]', timeout=120000)
                print("已成功进入商品页面，开始下载图片...")
            except PlaywrightTimeoutError:
                print("操作超时：未能在 2 分钟内检测到商品页面。")
                print("正在保存当前页面快照用于调试...")
                # Save screenshot and HTML for debugging
                screenshot_path = "debug_screenshot.png"
                html_path = "debug_page.html"
                try:
                    page.screenshot(path=screenshot_path)
                    with open(html_path, "w", encoding="utf-8") as f:
                        f.write(page.content())
                    print(f"已保存截图到: {os.path.abspath(screenshot_path)}")
                    print(f"已保存页面HTML到: {os.path.abspath(html_path)}")
                    print("请检查截图和HTML文件，确认登录后页面的实际内容。")
                except Exception as e:
                    print(f"保存调试文件时出错: {e}")
                browser.close()
                return None
            try:
                context.storage_state(path=str(state_path.resolve()))
            except Exception:
                pass

            product_title = ""
            try:
                def clean_title(t: str) -> str:
                    parts = [x.strip() for x in str(t or "").splitlines() if x.strip()]
                    t2 = parts[0] if parts else str(t or "")
                    t2 = re.sub(r"\s+", " ", t2).strip()
                    return t2

                selectors = [
                    "div.item-mb > a",
                    "div.item-mb a",
                    "h1",
                    ".detail-title",
                    ".goods-title",
                    ".item-title",
                    '[class*="title"] h1',
                    '[class*="title"]',
                ]
                for sel in selectors:
                    loc = page.locator(sel).first
                    if loc.count() == 0:
                        continue
                    text = clean_title(loc.inner_text(timeout=1500) or "")
                    if len(text) >= 4:
                        product_title = text
                        break
            except Exception:
                pass
            if not product_title:
                try:
                    meta = page.locator('meta[property="og:title"]').first
                    if meta.count() > 0:
                        product_title = (meta.get_attribute("content") or "").strip()
                except Exception:
                    pass

            product_price = None
            try:
                price_loc = page.locator("span.p_price.J_p_price").first
                if price_loc.count() > 0:
                    price_text = (price_loc.inner_text(timeout=1500) or "").strip()
                    m_price = re.search(r"(\d+(?:\.\d+)?)", price_text)
                    if m_price:
                        product_price = float(m_price.group(1))
            except Exception:
                pass

            # 从 URL 中提取商品 ID 或使用部分 URL 作为标题
            match = re.search(r'id=(\d+)', url)
            if match:
                title = f"商品ID_{match.group(1)}"
            else:
                # 如果没有ID，就用URL的一部分创建一个临时标题
                safe_url_part = re.sub(r'[^a-zA-Z0-9]', '_', url.split('.com/')[-1])
                title = safe_url_part[:50] # 截取前50个字符

            # 创建文件夹
            dir_path = os.path.join('products', title)
            os.makedirs(dir_path, exist_ok=True)

            # --- 下载商品图片 ---
            # 使用用户提供的精确选择器: img[data-haszoom="1"]
            product_image_elements = page.query_selector_all('img[data-haszoom="1"]')
            if product_image_elements:
                print(f"找到 {len(product_image_elements)} 张商品图片。")
                for i, img_element in enumerate(product_image_elements):
                    thumbnail_url = img_element.get_attribute('src')
                    if thumbnail_url:
                        # 移除URL中的缩略图后缀 (例如 _60x60.jpg)
                        full_size_url = re.sub(r'_\d+x\d+\.jpg$', '', thumbnail_url.strip())
                        
                        if not full_size_url.startswith('http'):
                            full_size_url = 'https:' + full_size_url

                        print(f"正在下载商品图 {i+1}: {full_size_url}")
                        try:
                            response = requests.get(full_size_url)
                            if response.status_code == 200:
                                image_path = os.path.join(dir_path, f'image_{i+1}.jpg')
                                with open(image_path, 'wb') as f:
                                    f.write(response.content)
                                print(f"成功下载商品图到: {image_path}")
                            else:
                                print(f"下载失败，状态码: {response.status_code}")
                        except Exception as e:
                            print(f"下载图片时出错: {e}")
            else:
                print("未找到任何带有 'data-haszoom=\"1\"' 属性的商品图片。")

            browser.close()
            return {
                "product_id": match.group(1) if match else None,
                "product_dir": dir_path,
                "title": product_title or title,
                "price": product_price,
                "url": url,
            }

    except Exception as e:
        print(f"使用 Playwright 下载图片时发生错误: {e}")
        return None

# --- 使用示例 ---
if __name__ == '__main__':
    # 将您想要爬取的 URL 放在这里
    target_url = 'https://www.wsy.com/item.htm?id=25014459'
    
    print("--- 开始使用 Playwright 下载商品图片 ---")
    download_images(target_url)
    print("-------------------------------------")

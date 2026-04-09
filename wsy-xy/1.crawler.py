import requests
from bs4 import BeautifulSoup
import urllib.parse

def crawl_products():
    """
    爬取网站以提取商品标题和 URL。

    本示例使用 'http://books.toscrape.com/'，这是一个专为网页抓取练习设计的网站。
    这里使用的选择器是针对这个特定网站的结构。如果你想爬取不同的网站，
    你需要检查其 HTML 结构并相应地调整选择器。

    返回:
        list: 一个字典列表，其中每个字典代表一个商品，包含其 'title' 和 'url'。
              如果请求失败或未找到商品，则返回空列表。
    """
    base_url = 'http://books.toscrape.com/'
    products = []

    try:
        # 发送 HTTP GET 请求
        response = requests.get(base_url)
        # 如果请求失败（例如，404 Not Found, 500 Internal Server Error），则抛出异常
        response.raise_for_status()

        # 使用 BeautifulSoup 解析 HTML 内容
        soup = BeautifulSoup(response.content, 'html.parser')

        # 查找所有商品信息块。在这个网站上，它们是 <article class="product_pod">
        product_pods = soup.find_all('article', class_='product_pod')

        for product in product_pods:
            # 标题在 <h3> 标签下的 <a> 标签中
            title_element = product.h3.a
            if title_element:
                title = title_element.get('title')
                
                # URL 是同一个 <a> 标签的 href 属性
                # 这是一个相对 URL，所以我们需要将其与 base_url 合并成一个绝对 URL
                relative_url = title_element.get('href')
                absolute_url = urllib.parse.urljoin(base_url, relative_url)
                
                products.append({'title': title, 'url': absolute_url})

    except requests.exceptions.RequestException as e:
        print(f"获取 URL 时出错: {e}")
        return []

    return products

# --- 使用示例 ---
if __name__ == '__main__':
    product_list = crawl_products()
    if product_list:
        print(f"找到了 {len(product_list)} 个商品:")
        # 打印前 5 个商品作为示例
        for i, product in enumerate(product_list[:5]):
            print(f"  {i+1}. 标题: {product['title']}")
            print(f"     URL: {product['url']}")
    else:
        print("没有找到任何商品。")

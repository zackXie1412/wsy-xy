import json
import os

def save_product_info(product):
    """
    将商品信息字典保存为格式化的 JSON 文件 (info.json)。

    这个函数会覆盖任何已存在的 info.json 文件。

    Args:
        product (dict): 包含商品信息的字典。
                        例如: {'title': '商品标题', 'price': 60, 'original_price': 100}
    """
    filename = "info.json"

    if not isinstance(product, dict):
        print(f"错误: 输入参数必须是一个字典，但收到了 {type(product)} 类型。")
        return

    try:
        with open(filename, 'w', encoding='utf-8') as f:
            # 使用 json.dump 将字典写入文件
            # ensure_ascii=False 确保中文字符能正确显示
            # indent=4 使 JSON 文件格式化，更易读
            json.dump(product, f, ensure_ascii=False, indent=4)
        
        print(f"商品信息已成功保存到: {os.path.abspath(filename)}")

    except Exception as e:
        print(f"保存文件 {filename} 时出错: {e}")

# --- 使用示例 ---
# 当这个脚本被直接运行时，以下代码会被执行，用于演示函数功能
if __name__ == '__main__':
    # 1. 定义一个示例商品信息
    sample_product_data = {
        'title': '爆款抖音得物高品质二开270G重磅美式高街曼巴棉潮牌圆领短袖T恤',
        'price': 16.00,
        'original_price': 80.00
    }
    
    # 2. 调用函数保存信息
    print("--- 正在调用 save_product_info 函数保存示例信息 ---")
    save_product_info(sample_product_data)
    print("-------------------------------------------------")

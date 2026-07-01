import requests

REPO = "Aisvbo/svb-bot"
BASE_RAW = f"https://huggingface.co/spaces/{REPO}/raw/main"
BASE_API = f"https://huggingface.co/api/spaces/{REPO}/tree/main"

def fetch_all():
    # 获取文件树
    resp = requests.get(BASE_API)
    resp.raise_for_status()
    tree = resp.json()
    
    content_list = []
    # 只抓取我们关心的文件后缀
    allowed_exts = ('.py', '.js', '.html', '.css', '.json', '.yaml', '.yml', '.txt', '.md')
    
    for item in tree:
        if item["type"] != "file":
            continue
        path = item["path"]
        if not path.endswith(allowed_exts):
            continue
        raw_url = f"{BASE_RAW}/{path}"
        try:
            code = requests.get(raw_url, timeout=10).text
            content_list.append(f"=== 📄 {path} ===\n{code}\n")
        except Exception as e:
            content_list.append(f"=== ❌ {path} (读取失败: {e}) ===\n")
    
    full_text = "\n".join(content_list)
    print(f"✅ 共抓取 {len(content_list)} 个文件，总字符数: {len(full_text)}")
    print("\n" + "="*50 + " 开始输出代码 " + "="*50 + "\n")
    print(full_text)

if __name__ == "__main__":
    fetch_all()
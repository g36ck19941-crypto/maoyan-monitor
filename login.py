import sys

from maoyan_client import MaoyanClient


def main():
    profile_dir = sys.argv[1] if len(sys.argv) > 1 else "./profile"

    client = MaoyanClient(user_data_dir=profile_dir, headless=False)
    client.start()

    print(f"请在弹出的浏览器里手动登录猫眼。")
    print(f"登录数据将保存到: {profile_dir}")
    print("登录完成后，回到这里按回车保存登录态并退出。")

    client.page.goto("https://www.maoyan.com/")
    input("登录完成后按回车键退出...")

    client.close()
    print(f"登录态已保存在 {profile_dir} 目录。")


if __name__ == "__main__":
    main()

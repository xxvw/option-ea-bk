#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TheOption自動売買ツール
指定した時間に買い/売りの取引を自動実行するツール
"""

import json
import time
import threading
import random
from datetime import datetime, timedelta
from typing import List, Dict, Any
import os
import sys
import shutil
import platform
import subprocess
import traceback
import logging
from logging.handlers import RotatingFileHandler

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager


class TheOptionTrader:
    """TheOption自動売買クラス"""
    
    def __init__(self, config_path: str = "config.json"):
        """
        初期化
        
        Args:
            config_path: 設定ファイルのパス
        """
        self.config_path = config_path
        self.config = self._load_config(config_path)
        self.driver = None
        self.is_running = False
        self.scheduled_trades = []
        self.logger = self._setup_logger()
        self.last_reset_date = None  # 最後にリセットした日付
        
    def _setup_logger(self) -> logging.Logger:
        """ログ設定を初期化"""
        logger = logging.getLogger('TheOptionTrader')
        logger.setLevel(logging.DEBUG)
        
        # 既存のハンドラーをクリア
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        
        # ログディレクトリを作成
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        
        # ファイルハンドラー（ローテーション付き）
        log_file = os.path.join(log_dir, "theoption_trader.log")
        file_handler = RotatingFileHandler(
            log_file, 
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        
        # コンソールハンドラー
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        
        # フォーマッター
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        # ハンドラーを追加
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        
        return logger
    
    def _log_error(self, error_type: str, error_message: str, exception: Exception = None, context: Dict[str, Any] = None):
        """
        エラーをログに記録
        
        Args:
            error_type: エラーの種類（例: 'ChromeDriver', 'Windows32', 'Selenium'）
            error_message: エラーメッセージ
            exception: 例外オブジェクト
            context: 追加のコンテキスト情報
        """
        # システム情報を取得
        system_info = {
            'os': platform.system(),
            'os_version': platform.release(),
            'architecture': platform.architecture()[0],
            'python_version': sys.version,
            'timestamp': datetime.now().isoformat()
        }
        
        # エラーの詳細情報を構築
        error_details = {
            'error_type': error_type,
            'error_message': error_message,
            'system_info': system_info
        }
        
        if exception:
            error_details['exception_type'] = type(exception).__name__
            error_details['exception_message'] = str(exception)
            error_details['traceback'] = traceback.format_exc()
        
        if context:
            error_details['context'] = context
        
        # ログに記録
        self.logger.error(f"[{error_type}] {error_message}", extra=error_details)
        
        # コンソールにも表示
        print(f"\n=== エラー発生 ===")
        print(f"種類: {error_type}")
        print(f"メッセージ: {error_message}")
        if exception:
            print(f"例外: {type(exception).__name__}: {str(exception)}")
        print(f"時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 30)
        
    def _deep_merge(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """
        辞書を深くマージする（overrideの値が優先）
        
        Args:
            base: ベースとなる辞書（デフォルト設定）
            override: 上書きする辞書（ユーザー設定）
        
        Returns:
            マージされた辞書
        """
        result = base.copy()
        
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                # 両方が辞書の場合は再帰的にマージ
                result[key] = self._deep_merge(result[key], value)
            else:
                # それ以外はoverrideの値で上書き
                result[key] = value
        
        return result
    
    def _load_default_config(self) -> Dict[str, Any]:
        """
        optフォルダからデフォルト設定を読み込み
        
        Returns:
            デフォルト設定の辞書、ファイルがない場合は空の辞書
        """
        default_config_path = os.path.join("opt", "config.json")
        
        try:
            if os.path.exists(default_config_path):
                with open(default_config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            else:
                print(f"情報: デフォルト設定ファイル '{default_config_path}' が見つかりません。")
                return {}
        except json.JSONDecodeError as e:
            print(f"警告: デフォルト設定ファイルの形式が正しくありません: {e}")
            return {}
        except Exception as e:
            print(f"警告: デフォルト設定ファイルの読み込み中にエラーが発生しました: {e}")
            return {}
    
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """設定ファイルを読み込み（デフォルト設定とマージ）"""
        # まずデフォルト設定を読み込み
        default_config = self._load_default_config()
        
        # ユーザー設定を読み込み
        user_config = {}
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
        except FileNotFoundError as e:
            if not default_config:
                self._log_error("ConfigFile", f"設定ファイル '{config_path}' が見つかりません", e)
                print(f"設定ファイル '{config_path}' が見つかりません。")
                sys.exit(1)
            else:
                print(f"情報: 設定ファイル '{config_path}' が見つかりません。デフォルト設定を使用します。")
        except json.JSONDecodeError as e:
            self._log_error("ConfigFile", f"設定ファイルの形式が正しくありません: {e}", e)
            print(f"設定ファイルの形式が正しくありません: {e}")
            sys.exit(1)
        
        # デフォルト設定とユーザー設定をマージ（ユーザー設定が優先）
        if default_config:
            merged_config = self._deep_merge(default_config, user_config)
            
            # 不足していた設定があるかチェック
            missing_keys = self._find_missing_keys(default_config, user_config)
            if missing_keys:
                print(f"\n=== デフォルト設定から補完された項目 ===")
                for key_path in missing_keys:
                    print(f"  - {key_path}")
                print("=" * 40)
            
            # マージ完了後、optフォルダを削除
            self._cleanup_opt_folder()
            
            return merged_config
        else:
            return user_config
    
    def _cleanup_opt_folder(self):
        """
        optフォルダを削除する（マージ完了後のクリーンアップ）
        """
        opt_folder = "opt"
        
        try:
            if os.path.exists(opt_folder):
                shutil.rmtree(opt_folder)
                print(f"情報: '{opt_folder}' フォルダを削除しました（設定マージ完了）")
        except Exception as e:
            print(f"警告: '{opt_folder}' フォルダの削除に失敗しました: {e}")
    
    def _find_missing_keys(self, default: Dict[str, Any], user: Dict[str, Any], prefix: str = "") -> List[str]:
        """
        ユーザー設定に不足しているキーを見つける
        
        Args:
            default: デフォルト設定
            user: ユーザー設定
            prefix: キーパスのプレフィックス
        
        Returns:
            不足しているキーパスのリスト
        """
        missing = []
        
        for key, value in default.items():
            key_path = f"{prefix}.{key}" if prefix else key
            
            if key not in user:
                missing.append(key_path)
            elif isinstance(value, dict) and isinstance(user.get(key), dict):
                # 両方が辞書の場合は再帰的にチェック
                missing.extend(self._find_missing_keys(value, user[key], key_path))
        
        return missing
    
    def reload_config(self):
        """
        設定ファイルを再読み込みし、スケジュールを再設定
        
        Returns:
            bool: 再読み込みに成功したらTrue
        """
        try:
            print("\n=== 設定ファイルを再読み込み中 ===")
            
            # 設定ファイルを再読み込み
            new_config = self._load_config(self.config_path)
            self.config = new_config
            print("設定ファイルを再読み込みしました")
            
            # スケジュールをクリアして再設定
            self.scheduled_trades = []
            self.schedule_trades()
            
            print(f"スケジュールを再設定しました: {len(self.scheduled_trades)}件")
            print("=" * 40)
            
            return True
            
        except Exception as e:
            self._log_error("ConfigFile", f"設定ファイルの再読み込みに失敗しました: {e}", e)
            print(f"エラー: 設定ファイルの再読み込みに失敗しました: {e}")
            return False
    
    def _check_daily_reset(self):
        """
        毎日朝7時に設定をリセットするかチェック
        
        Returns:
            bool: リセットが実行された場合はTrue
        """
        now = datetime.now()
        today = now.date()
        
        # 今日のリセットがまだ実行されていない場合
        if self.last_reset_date != today:
            # 朝7時以降かチェック
            if now.hour >= 7:
                print(f"\n=== 毎日のリセット（朝7時）を実行中 ===")
                print(f"現在時刻: {now.strftime('%Y-%m-%d %H:%M:%S')}")
                
                # 設定を再読み込み
                if self.reload_config():
                    self.last_reset_date = today
                    print(f"リセット完了: 次回リセットは {today + timedelta(days=1)} 07:00 以降")
                    return True
                else:
                    print("警告: リセットに失敗しましたが、続行します")
        
        return False
    
    def _get_chrome_version(self) -> str:
        """Chromeブラウザのバージョンを取得"""
        try:
            system = platform.system()
            if system == "Windows":
                # Windowsの場合
                import winreg
                try:
                    # レジストリからChromeのバージョンを取得
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon")
                    version, _ = winreg.QueryValueEx(key, "version")
                    winreg.CloseKey(key)
                    return version
                except:
                    # コマンドラインから取得を試行
                    result = subprocess.run([
                        'reg', 'query', 
                        'HKEY_CURRENT_USER\\Software\\Google\\Chrome\\BLBeacon', 
                        '/v', 'version'
                    ], capture_output=True, text=True)
                    if result.returncode == 0:
                        lines = result.stdout.strip().split('\n')
                        for line in lines:
                            if 'version' in line:
                                return line.split()[-1]
            elif system == "Darwin":  # macOS
                result = subprocess.run([
                    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--version'
                ], capture_output=True, text=True)
                if result.returncode == 0:
                    return result.stdout.strip().split()[-1]
            elif system == "Linux":
                result = subprocess.run(['google-chrome', '--version'], capture_output=True, text=True)
                if result.returncode == 0:
                    return result.stdout.strip().split()[-1]
        except Exception as e:
            self._log_error("ChromeVersion", f"Chromeバージョン取得エラー: {e}", e)
            print(f"Chromeバージョン取得エラー: {e}")
        
        return "不明"
    
    def _diagnose_chromedriver_issue(self):
        """ChromeDriverの問題を診断"""
        print("\n=== ChromeDriver診断情報 ===")
        
        # システム情報
        print(f"OS: {platform.system()} {platform.release()}")
        print(f"アーキテクチャ: {platform.architecture()[0]}")
        
        # Chromeバージョン
        chrome_version = self._get_chrome_version()
        print(f"Chromeバージョン: {chrome_version}")
        
        # webdriver_managerのキャッシュディレクトリ
        try:
            from webdriver_manager.core.utils import get_browser_version_from_os
            from webdriver_manager.core.driver_cache import DriverCacheManager
            
            cache_manager = DriverCacheManager()
            cache_path = cache_manager.get_cache_path()
            print(f"webdriver_managerキャッシュパス: {cache_path}")
            
            if os.path.exists(cache_path):
                cache_files = os.listdir(cache_path)
                print(f"キャッシュファイル数: {len(cache_files)}")
                for file in cache_files[:5]:  # 最初の5個のファイルを表示
                    file_path = os.path.join(cache_path, file)
                    if os.path.isfile(file_path):
                        size = os.path.getsize(file_path)
                        print(f"  - {file}: {size} bytes")
            else:
                print("キャッシュディレクトリが存在しません")
        except Exception as e:
            print(f"キャッシュ情報取得エラー: {e}")
        
        # システムPATHのChromeDriver
        chromedriver_path = shutil.which("chromedriver") or shutil.which("chromedriver.exe")
        if chromedriver_path:
            print(f"システムPATHのChromeDriver: {chromedriver_path}")
            try:
                size = os.path.getsize(chromedriver_path)
                print(f"ファイルサイズ: {size} bytes")
            except:
                print("ファイルサイズ取得失敗")
        else:
            print("システムPATHにChromeDriverが見つかりません")
        
        print("=" * 30)
    
    def _check_windows32_errors(self, error_messages: List[str]):
        """Windows32エラーをチェックして特別なログを出力"""
        windows32_keywords = [
            'windows32', 'win32', 'access denied', 'permission denied',
            'file not found', 'path not found', 'system cannot find',
            'chrome.exe', 'chromedriver.exe', 'executable'
        ]
        
        windows32_errors = []
        for msg in error_messages:
            if any(keyword.lower() in msg.lower() for keyword in windows32_keywords):
                windows32_errors.append(msg)
        
        if windows32_errors:
            self._log_error("Windows32", "Windows32関連のエラーが検出されました", context={
                'windows32_errors': windows32_errors,
                'system': platform.system(),
                'architecture': platform.architecture()[0]
            })
            
            print("\n=== Windows32エラー検出 ===")
            print("以下のWindows32関連エラーが検出されました:")
            for i, error in enumerate(windows32_errors, 1):
                print(f"{i}. {error}")
            print("\nWindows32エラーの解決方法:")
            print("1. 管理者権限でコマンドプロンプトを実行してください")
            print("2. Chromeブラウザを完全に終了してから再実行してください")
            print("3. ウイルス対策ソフトがChromeDriverをブロックしていないか確認してください")
            print("4. Windows Defenderの除外設定にChromeDriverのパスを追加してください")
            print("5. 一時的にウイルス対策ソフトを無効にしてテストしてください")
            print("=" * 40)
    
    def _setup_chrome_driver(self) -> webdriver.Chrome:
        """Chromeドライバーをセットアップ"""
        chrome_options = Options()
        
        # プロファイルディレクトリの設定
        profile_dir = self.config['browser_settings']['profile_directory']
        if not os.path.isabs(profile_dir):
            profile_dir = os.path.abspath(profile_dir)
        
        # プロファイルディレクトリが存在しない場合は作成
        if not os.path.exists(profile_dir):
            os.makedirs(profile_dir, exist_ok=True)
            print(f"Chromeプロファイルディレクトリを作成しました: {profile_dir}")
        
        chrome_options.add_argument(f"--user-data-dir={profile_dir}")
        
        # ヘッドレスモードの設定
        if self.config['browser_settings']['headless']:
            chrome_options.add_argument("--headless")
        
        # ウィンドウサイズの設定
        window_size = self.config['browser_settings']['window_size']
        chrome_options.add_argument(f"--window-size={window_size['width']},{window_size['height']}")
        
        # その他のオプション
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--allow-running-insecure-content")
        
        # ChromeDriverの設定を試行
        driver = None
        error_messages = []
        
        # 方法1: webdriver_managerを使用（キャッシュをクリア）
        try:
            print("ChromeDriverを設定中... (webdriver_manager使用)")
            
            # キャッシュディレクトリを完全にクリア
            try:
                from webdriver_manager.core.driver_cache import DriverCacheManager
                cache_manager = DriverCacheManager()
                cache_path = cache_manager.get_cache_path()
                
                if os.path.exists(cache_path):
                    print(f"キャッシュディレクトリをクリア中: {cache_path}")
                    import shutil
                    shutil.rmtree(cache_path, ignore_errors=True)
                    print("キャッシュディレクトリを削除しました")
            except Exception as cache_error:
                print(f"キャッシュクリア中のエラー（続行します）: {cache_error}")
            
            # ChromeDriverを新規ダウンロード
            driver_manager = ChromeDriverManager()
            driver_path = driver_manager.install()
            print(f"ChromeDriverパス: {driver_path}")
            
            # ファイルが実行可能かチェック
            if os.path.exists(driver_path):
                file_size = os.path.getsize(driver_path)
                print(f"ChromeDriverファイルサイズ: {file_size} bytes")
                
                if file_size < 1000:  # ファイルサイズが小さすぎる場合は破損している可能性
                    raise Exception(f"ChromeDriverファイルが破損している可能性があります (サイズ: {file_size} bytes)")
                
                # ファイルの実行権限を確認・設定（Unix系OSの場合）
                if platform.system() != "Windows":
                    import stat
                    current_permissions = os.stat(driver_path).st_mode
                    if not (current_permissions & stat.S_IXUSR):
                        os.chmod(driver_path, current_permissions | stat.S_IXUSR)
                        print("ChromeDriverに実行権限を付与しました")
            
            service = Service(driver_path)
            driver = webdriver.Chrome(service=service, options=chrome_options)
            print("ChromeDriverの設定が完了しました (webdriver_manager)")
            return driver
            
        except Exception as e:
            error_msg = f"webdriver_manager使用時のエラー: {e}"
            error_messages.append(error_msg)
            self._log_error("ChromeDriver", error_msg, e, {
                'method': 'webdriver_manager',
                'chrome_version': self._get_chrome_version(),
                'system': platform.system()
            })
            print(f"警告: {error_msg}")
        
        # 方法2: システムのPATHからChromeDriverを探す
        try:
            print("ChromeDriverを設定中... (システムPATH使用)")
            # システムのPATHからchromedriver.exeを探す
            import shutil
            chromedriver_path = shutil.which("chromedriver") or shutil.which("chromedriver.exe")
            
            if chromedriver_path:
                print(f"システムPATHでChromeDriverを発見: {chromedriver_path}")
                service = Service(chromedriver_path)
                driver = webdriver.Chrome(service=service, options=chrome_options)
                print("ChromeDriverの設定が完了しました (システムPATH)")
                return driver
            else:
                raise Exception("システムPATHにChromeDriverが見つかりません")
                
        except Exception as e:
            error_msg = f"システムPATH使用時のエラー: {e}"
            error_messages.append(error_msg)
            self._log_error("ChromeDriver", error_msg, e, {
                'method': 'system_path',
                'chromedriver_path': chromedriver_path,
                'system': platform.system()
            })
            print(f"警告: {error_msg}")
        
        # 方法3: Serviceを使わずに直接実行を試行
        try:
            print("ChromeDriverを設定中... (Service無し)")
            driver = webdriver.Chrome(options=chrome_options)
            print("ChromeDriverの設定が完了しました (Service無し)")
            return driver
            
        except Exception as e:
            error_msg = f"Service無し実行時のエラー: {e}"
            error_messages.append(error_msg)
            self._log_error("ChromeDriver", error_msg, e, {
                'method': 'no_service',
                'system': platform.system()
            })
            print(f"警告: {error_msg}")
        
        # すべての方法が失敗した場合
        print("\n=== ChromeDriverの設定に失敗しました ===")
        print("以下のエラーが発生しました:")
        for i, msg in enumerate(error_messages, 1):
            print(f"{i}. {msg}")
        
        # Windows32エラーを特別にチェック
        self._check_windows32_errors(error_messages)
        
        # 診断情報を表示
        self._diagnose_chromedriver_issue()
        
        print("\n解決方法:")
        print("1. Chromeブラウザが最新版にアップデートされているか確認してください")
        print("2. 以下のコマンドでwebdriver_managerのキャッシュをクリアしてください:")
        print("   pip uninstall webdriver-manager")
        print("   pip install webdriver-manager")
        print("3. 手動でChromeDriverをダウンロードしてPATHに追加してください:")
        print("   https://chromedriver.chromium.org/downloads")
        print("4. Chromeブラウザのバージョンを確認し、対応するChromeDriverをダウンロードしてください")
        print("5. webdriver_managerのキャッシュディレクトリを手動で削除してください:")
        
        try:
            from webdriver_manager.core.driver_cache import DriverCacheManager
            cache_manager = DriverCacheManager()
            cache_path = cache_manager.get_cache_path()
            print(f"   キャッシュパス: {cache_path}")
        except:
            print("   キャッシュパスの取得に失敗しました")
        
        raise Exception("ChromeDriverの設定に失敗しました。上記の解決方法を試してください。")
    
    def start_browser(self):
        """ブラウザを起動してTheOptionサイトにアクセス"""
        print("ブラウザを起動しています...")
        self.driver = self._setup_chrome_driver()
        
        # ログイン画面にアクセス
        login_url = self.config['theoption_settings']['login_url']
        print(f"TheOptionログイン画面にアクセスしています: {login_url}")
        self.driver.get(login_url)
        
        print("ブラウザが起動しました。手動でログインを行ってください。")
        print("ログイン完了後、Enterキーを押してください...")
        input()
        
        # ログイン後、取引画面に移動
        trading_url = self.config['theoption_settings']['trading_url']
        print(f"取引画面に移動しています: {trading_url}")
        self.driver.get(trading_url)
        
        # 取引画面の読み込み待機
        time.sleep(3)
        print("取引画面の準備が完了しました。")
    
    def set_amount(self, amount: str):
        """
        取引金額を設定
        
        Args:
            amount: 設定する金額（文字列）
        """
        amount_selector = self.config['theoption_settings']['amount_input_selector']
        
        if not amount_selector:
            print("警告: 金額入力欄のセレクターが設定されていません。")
            return False
        
        try:
            # 金額入力欄を探す
            amount_input = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, amount_selector))
            )
            
            # 入力欄をクリックしてフォーカス
            amount_input.click()
            time.sleep(0.1)
            
            # 既存の値を全選択して削除
            amount_input.send_keys(Keys.CONTROL + "a")
            time.sleep(0.05)
            amount_input.send_keys(Keys.DELETE)
            time.sleep(0.1)
            
            # 新しい金額を入力
            amount_input.send_keys(amount)
            time.sleep(0.1)
            
            print(f"  → 金額を設定しました: {amount}")
            return True
            
        except TimeoutException as e:
            self._log_error("Selenium", "金額入力欄が見つかりません", e, {
                'selector': amount_selector,
                'action': 'set_amount'
            })
            print("エラー: 金額入力欄が見つかりません")
            return False
        except Exception as e:
            self._log_error("Selenium", f"金額設定中にエラーが発生しました: {e}", e, {
                'selector': amount_selector,
                'action': 'set_amount'
            })
            print(f"エラー: 金額設定中にエラーが発生しました: {e}")
            return False
    
    def get_current_currency_from_display(self):
        """
        ブラウザ画面に表示されている現在の通貨を取得
        
        Returns:
            str: 現在表示されている通貨名、取得できない場合はNone
        """
        try:
            # 画面に表示されている通貨を取得するための複数のセレクターを試行
            selectors = [
                # 選択された通貨を示す可能性のあるセレクター
                "div.assetsListWrap li.selected .assetName",
                "div.assetsListWrap li.active .assetName",
                "div.assetsListWrap li.selected i.assetLabel",
                "div.assetsListWrap li.active i.assetLabel",
                # より一般的なセレクター
                ".assetName",
                "i.assetLabel",
            ]
            
            for selector in selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        # 親要素が選択状態かどうかを確認
                        try:
                            parent = element.find_element(By.XPATH, "./ancestor::li[contains(@class, 'selected') or contains(@class, 'active')]")
                            currency_text = element.text.strip()
                            if currency_text and ('/' in currency_text or len(currency_text) > 0):
                                print(f"  → 現在表示されている通貨を取得しました: {currency_text}")
                                return currency_text
                        except:
                            # 親要素の確認に失敗した場合、要素のテキストを直接確認
                            currency_text = element.text.strip()
                            if currency_text and '/' in currency_text:
                                print(f"  → 現在表示されている通貨を取得しました: {currency_text}")
                                return currency_text
                except:
                    continue
            
            # 上記で見つからない場合、assetsListWrap内のすべての要素を確認
            try:
                parent_elements = self.driver.find_elements(By.CSS_SELECTOR, "div.assetsListWrap li")
                for parent_element in parent_elements:
                    class_names = parent_element.get_attribute("class") or ""
                    if "selected" in class_names.lower() or "active" in class_names.lower():
                        # 子要素から通貨名を取得
                        try:
                            currency_element = parent_element.find_element(By.CSS_SELECTOR, ".assetName, i.assetLabel")
                            currency_text = currency_element.text.strip()
                            if currency_text:
                                print(f"  → 現在表示されている通貨を取得しました: {currency_text}")
                                return currency_text
                        except:
                            continue
            except:
                pass
            
            print("警告: 現在表示されている通貨を取得できませんでした。")
            return None
            
        except Exception as e:
            print(f"エラー: 現在表示されている通貨取得中にエラーが発生しました: {e}")
            return None
    
    def select_trading_time(self, trading_time: str):
        """
        取引時間を選択
        
        Args:
            trading_time: 選択する取引時間（例: "1分", "5分"）
        """
        time_dropdown_selector = self.config['theoption_settings']['time_dropdown_selector']
        time_list_selector = self.config['theoption_settings']['time_list_selector']
        
        if not time_dropdown_selector or not time_list_selector:
            print("警告: 取引時間選択のセレクターが設定されていません。")
            return False
        
        try:
            # 取引時間ドロップダウンをクリック
            dropdown = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, time_dropdown_selector))
            )
            dropdown.click()
            time.sleep(0.5)
            
            # 取引時間リストを取得
            time_elements = WebDriverWait(self.driver, 5).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, time_list_selector))
            )
            
            # 指定した取引時間を検索してクリック
            for element in time_elements:
                element_text = element.text.strip()
                if trading_time in element_text or element_text in trading_time:
                    element.click()
                    print(f"  → 取引時間を選択しました: {element_text}")
                    time.sleep(0.3)
                    return True
            
            print(f"エラー: 指定された取引時間 '{trading_time}' が見つかりません")
            # ドロップダウンを閉じるためにESCキーを押す
            self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            return False
            
        except TimeoutException as e:
            self._log_error("Selenium", "取引時間選択要素が見つかりません", e, {
                'trading_time': trading_time,
                'dropdown_selector': time_dropdown_selector,
                'list_selector': time_list_selector,
                'action': 'select_trading_time'
            })
            print("エラー: 取引時間選択要素が見つかりません")
            return False
        except Exception as e:
            self._log_error("Selenium", f"取引時間選択中にエラーが発生しました: {e}", e, {
                'trading_time': trading_time,
                'dropdown_selector': time_dropdown_selector,
                'list_selector': time_list_selector,
                'action': 'select_trading_time'
            })
            print(f"エラー: 取引時間選択中にエラーが発生しました: {e}")
            return False
    
    def get_current_currency(self):
        """
        現在選択されている通貨を取得（get_current_currency_from_displayのエイリアス）
        
        Returns:
            str: 現在選択されている通貨名、取得できない場合はNone
        """
        return self.get_current_currency_from_display()
    
    def get_current_trading_time(self):
        """
        現在選択されている取引時間を取得
        
        Returns:
            str: 現在選択されている取引時間、取得できない場合はNone
        """
        try:
            # 取引時間表示エリアから現在の時間を取得
            time_dropdown_selector = self.config['theoption_settings']['time_dropdown_selector']
            
            if not time_dropdown_selector:
                print("警告: 取引時間ドロップダウンのセレクターが設定されていません。")
                return None
            
            # 現在選択されている取引時間の表示要素を探す
            current_time_selectors = [
                # 選択された時間を示す可能性のあるセレクター
                f"{time_dropdown_selector} .selected",
                f"{time_dropdown_selector} .active",
                f"{time_dropdown_selector} .dd__selected",
                f"{time_dropdown_selector} .dd__placeholder",
                # より具体的なセレクター（config.jsonの構造から推測）
                "#root > div > div.jss3 > div.MuiGrid-root.MuiGrid-container.jss12.css-1d3bbye > div.jss13 > div > div.MuiGrid-root.MuiGrid-item.MuiGrid-grid-lg-12.jss15.css-q562m3 > div > div.selectedTbZone.expiryZone > div:nth-child(1) > div > div.dd__wrapper .dd__selected",
                "#root > div > div.jss3 > div.MuiGrid-root.MuiGrid-container.jss12.css-1d3bbye > div.jss13 > div > div.MuiGrid-root.MuiGrid-item.MuiGrid-grid-lg-12.jss15.css-q562m3 > div > div.selectedTbZone.expiryZone > div:nth-child(1) > div > div.dd__wrapper .dd__placeholder"
            ]
            
            for selector in current_time_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        time_text = element.text.strip()
                        if time_text and ('秒' in time_text or '分' in time_text):  # 時間の形式をチェック
                            print(f"  → 現在の取引時間を取得しました: {time_text}")
                            return time_text
                except:
                    continue
            
            print("警告: 現在選択されている取引時間を取得できませんでした。デフォルト時間を使用します。")
            return None
            
        except Exception as e:
            print(f"エラー: 現在の取引時間取得中にエラーが発生しました: {e}")
            return None
        
    def get_entry_count(self):
        """
        現在のエントリー本数を取得（div.timer-areaの個数）
        
        Returns:
            int: エントリー本数、取得できない場合は0
        """
        try:
            timer_areas = self.driver.find_elements(By.CSS_SELECTOR, "div.timer-area")
            count = len(timer_areas)
            return count
        except Exception as e:
            print(f"警告: エントリー本数の取得中にエラーが発生しました: {e}")
            return 0
    
    def is_oneclick_trading_enabled(self):
        """
        ワンクリック注文が有効かどうかを確認
        
        Returns:
            bool: ワンクリック注文が有効ならTrue
        """
        try:
            # 購入ボタンが存在しない場合、ワンクリック注文が有効
            purchase_button_selector = self.config['theoption_settings']['purchase_button_selector']
            purchase_buttons = self.driver.find_elements(By.CSS_SELECTOR, purchase_button_selector)
            return len(purchase_buttons) == 0
        except Exception as e:
            print(f"警告: ワンクリック注文の状態確認中にエラーが発生しました: {e}")
            return False
    
    def enable_oneclick_trading(self):
        """
        ワンクリック注文を有効にする
        
        Returns:
            bool: 有効化に成功したらTrue
        """
        try:
            # 既にワンクリック注文が有効かチェック
            if self.is_oneclick_trading_enabled():
                print("  → ワンクリック注文は既に有効です")
                return True
            
            # ワンクリック注文のトグルセレクターを取得
            oneclick_selector = self.config['theoption_settings'].get('oneclick_toggle_selector')
            
            if not oneclick_selector:
                print("警告: ワンクリック注文のセレクターが設定されていません")
                return False
            
            # トグルをクリック
            toggle_element = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, oneclick_selector))
            )
            toggle_element.click()
            time.sleep(0.3)
            
            # 有効化を確認
            if self.is_oneclick_trading_enabled():
                print("  → ワンクリック注文を有効にしました")
                return True
            else:
                print("警告: ワンクリック注文の有効化に失敗しました")
                return False
                
        except TimeoutException as e:
            self._log_error("Selenium", "ワンクリック注文のトグルが見つかりません", e)
            print("エラー: ワンクリック注文のトグルが見つかりません")
            return False
        except Exception as e:
            self._log_error("Selenium", f"ワンクリック注文の有効化中にエラーが発生しました: {e}", e)
            print(f"エラー: ワンクリック注文の有効化中にエラーが発生しました: {e}")
            return False
    
    def wait_for_entry_count(self, target_count: int, initial_count: int = None, timeout_seconds: float = None):
        """
        指定したエントリー本数になるまで待機
        
        Args:
            target_count: 目標のエントリー本数
            initial_count: 初期のエントリー本数（指定しない場合は現在の本数を取得）
            timeout_seconds: タイムアウト秒数（設定ファイルから取得、デフォルト10秒）
        
        Returns:
            bool: 目標本数に達した場合はTrue、タイムアウトした場合はFalse
        """
        if timeout_seconds is None:
            timeout_seconds = self.config['theoption_settings'].get('retry_seconds', 10)
        
        if initial_count is None:
            initial_count = self.get_entry_count()
        
        start_time = time.time()
        check_interval = 0.1  # 100msごとにチェック
        
        print(f"  → エントリー本数確認: 現在={initial_count}本, 目標={target_count}本 (タイムアウト={timeout_seconds}秒)")
        
        while time.time() - start_time < timeout_seconds:
            current_count = self.get_entry_count()
            
            if current_count >= target_count:
                print(f"  → エントリー本数が目標に達しました: {current_count}本")
                return True
            
            time.sleep(check_interval)
        
        # タイムアウト
        final_count = self.get_entry_count()
        print(f"  → タイムアウト: エントリー本数={final_count}本 (目標={target_count}本)")
        return False
    
    def close_browser(self):
        """ブラウザを閉じる"""
        if self.driver:
            self.driver.quit()
            self.driver = None
            print("ブラウザを閉じました。")
    
    def clear_browser_cache_and_reload(self):
        """
        ブラウザのキャッシュをクリアしてページをリロード
        前回のエントリー本数などのキャッシュを削除する
        """
        if not self.driver:
            print("エラー: ブラウザが起動していません。")
            return False
        
        try:
            print("  → ブラウザキャッシュをクリア中...")
            
            # ローカルストレージとセッションストレージをクリア
            self.driver.execute_script("window.localStorage.clear();")
            self.driver.execute_script("window.sessionStorage.clear();")
            
            # ページをリロード
            print("  → ページをリロード中...")
            self.driver.refresh()
            
            # ページの読み込み待機
            time.sleep(2)
            
            print("  → キャッシュクリアとリロードが完了しました")
            return True
            
        except Exception as e:
            print(f"警告: キャッシュクリア中にエラーが発生しました: {e}")
            return False
    
    def is_purchase_button_available(self):
        """
        購入ボタンが利用可能かどうかを確認
        
        Returns:
            bool: 購入ボタンが存在し、クリック可能ならTrue
        """
        try:
            purchase_button_selector = self.config['theoption_settings']['purchase_button_selector']
            purchase_buttons = self.driver.find_elements(By.CSS_SELECTOR, purchase_button_selector)
            return len(purchase_buttons) > 0
        except Exception as e:
            print(f"警告: 購入ボタンの確認中にエラーが発生しました: {e}")
            return False
    
    def execute_trade(self, direction: str, count: int = 1, amount: str = None, trading_time: str = None, retry_seconds: float = None, reset_entry_count: bool = False):
        """
        取引を実行
        
        Args:
            direction: 取引方向 ('buy' または 'sell')
            count: 実行回数（エントリー本数）
            amount: 取引金額（指定しない場合はデフォルト値を使用）
            trading_time: 取引時間（指定しない場合はデフォルト値を使用）
            retry_seconds: リトライ秒数（指定しない場合はデフォルト値を使用）
            reset_entry_count: エントリー本数をリセットするか（Trueの場合、初期エントリー本数を0として扱う）
        """
        if not self.driver:
            print("エラー: ブラウザが起動していません。")
            return False
        
        try:
            # 取引方向に応じてボタンセレクターを選択
            if direction.lower() == 'buy':
                direction_button_selector = self.config['theoption_settings']['buy_button_selector']
                action_name = "買い"
            elif direction.lower() == 'sell':
                direction_button_selector = self.config['theoption_settings']['sell_button_selector']
                action_name = "売り"
            else:
                print(f"エラー: 無効な取引方向 '{direction}' です。")
                return False
            
            # 購入ボタンのセレクター
            purchase_button_selector = self.config['theoption_settings']['purchase_button_selector']
            wait_time = self.config['theoption_settings'].get('wait_time_between_actions', 0.5)
            
            # 設定値の決定（指定されていない場合はデフォルト値を使用）
            trade_amount = amount or self.config['theoption_settings']['default_amount']
            trade_time = trading_time or self.config['theoption_settings']['default_time']
            trade_retry_seconds = retry_seconds if retry_seconds is not None else self.config['theoption_settings'].get('retry_seconds', 10)
            
            # ワンクリック注文を使用するかどうか
            use_oneclick = self.config['theoption_settings'].get('use_oneclick_trading', False)
            
            # 現在表示されている通貨を取得
            current_currency = self.get_current_currency_from_display()
            trade_currency = current_currency if current_currency else "（表示中の通貨）"
            
            # ボタンセレクターが設定されていない場合の警告
            if not direction_button_selector:
                print(f"警告: {action_name}ボタンのセレクターが設定されていません。")
                print("config.jsonでセレクターを設定してください。")
                return False
            
            # ワンクリック注文を使用しない場合のみ購入ボタンセレクターをチェック
            if not use_oneclick and not purchase_button_selector:
                print("警告: 購入ボタンのセレクターが設定されていません。")
                print("config.jsonでpurchase_button_selectorを設定してください。")
                return False
            
            # ワンクリック注文を使用しない場合、購入ボタンが画面上に存在するかチェック
            if not use_oneclick:
                if not self.is_purchase_button_available():
                    print("警告: 購入ボタンが見つかりません。この取引をスキップします。")
                    print("  → ワンクリック注文が有効になっている可能性があります。")
                    return False
            
            # 初期のエントリー本数を取得
            if reset_entry_count:
                # スケジュール実行時はエントリー本数をリセット（実際の本数を基準として、count本を追加）
                base_entry_count = self.get_entry_count()
                initial_entry_count = 0  # 計算用（リセット扱い）
                print(f"  → エントリー本数をリセットしました（スケジュール実行のため）")
                print(f"  → 現在の画面エントリー本数: {base_entry_count}本（基準値として使用）")
            else:
                base_entry_count = self.get_entry_count()
                initial_entry_count = base_entry_count
            target_entry_count = base_entry_count + count
            
            print(f"取引実行開始: {action_name}方向 x{count}本")
            print(f"  通貨: {trade_currency}")
            print(f"  時間: {trade_time} (システム設定)")
            print(f"  金額: {trade_amount}")
            print(f"  リトライ秒数: {trade_retry_seconds}秒")
            print(f"  ワンクリック注文: {'有効' if use_oneclick else '無効'}")
            print(f"  現在のエントリー本数: {initial_entry_count}本")
            print(f"  目標エントリー本数: {target_entry_count}本")
            
            # 全体のタイムアウト開始時刻を記録
            trade_start_time = time.time()
            
            # 1. まず取引時間を設定（システム設定から）
            print(f"\n取引時間を設定中: {trade_time}")
            if not self.select_trading_time(trade_time):
                print(f"警告: 取引時間の設定に失敗しました。現在の設定で続行します。")
            
            # 2. ワンクリック注文を有効化（設定で有効な場合）
            if use_oneclick:
                print("\nワンクリック注文を確認中...")
                if not self.is_oneclick_trading_enabled():
                    self.enable_oneclick_trading()
            
            # 指定回数分取引を実行
            for i in range(count):
                # 全体のタイムアウトをチェック
                elapsed_time = time.time() - trade_start_time
                if elapsed_time >= trade_retry_seconds:
                    print(f"\n  → 全体タイムアウト: {trade_retry_seconds}秒を超過しました（経過時間: {elapsed_time:.2f}秒）")
                    print(f"  → 残りのエントリーをスキップします（{i}/{count}本完了）")
                    break
                
                remaining_time = trade_retry_seconds - elapsed_time
                
                try:
                    print(f"\n取引実行中 ({i+1}/{count}): {action_name}方向 （残り時間: {remaining_time:.1f}秒）")
                    
                    # 1. 金額を設定
                    if not self.set_amount(trade_amount):
                        print(f"  → 金額設定に失敗しました ({i+1}/{count})")
                        continue
                    time.sleep(wait_time * 0.3)
                    
                    # 2. ワンクリック注文の場合
                    if use_oneclick:
                        # HIGH/LOWボタンをクリックするだけでエントリー（購入ボタンは押さない）
                        direction_button = WebDriverWait(self.driver, 2).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, direction_button_selector))
                        )
                        direction_button.click()
                        print(f"  → {action_name}ボタンをクリックしました（ワンクリック注文 - 購入ボタンなし）")
                        time.sleep(wait_time * 0.5)
                    else:
                        # 通常モード
                        # 3. BUY/SELLボタンをクリック（1本目のみ）
                        if i == 0:
                            direction_button = WebDriverWait(self.driver, 5).until(
                                EC.element_to_be_clickable((By.CSS_SELECTOR, direction_button_selector))
                            )
                            direction_button.click()
                            print(f"  → {action_name}ボタンをクリックしました")
                            time.sleep(wait_time)
                        else:
                            print(f"  → {action_name}ボタンのクリックをスキップ（2本目以降）")
                        
                        # 4. 購入ボタンをクリック
                        purchase_button = WebDriverWait(self.driver, 5).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, purchase_button_selector))
                        )
                        purchase_button.click()
                        print(f"  → 購入ボタンをクリックしました")
                        time.sleep(wait_time)
                    
                    # 画面反映を待つ
                    time.sleep(0.5)
                    
                    # 5. エントリー本数を確認
                    current_entry_count = self.get_entry_count()
                    expected_count = base_entry_count + (i + 1)
                    
                    if current_entry_count >= expected_count:
                        print(f"  → {action_name}取引を実行しました ({i+1}/{count})")
                        continue
                    
                    # エントリー本数が不足している場合、リトライ
                    print(f"  → エントリー本数が不足しています。リトライします...")
                    retry_start_time = time.time()
                    retry_check_interval = 0.1
                    
                    while True:
                        # 全体タイムアウトをチェック
                        total_elapsed = time.time() - trade_start_time
                        if total_elapsed >= trade_retry_seconds:
                            print(f"  → 全体タイムアウト: {trade_retry_seconds}秒を超過しました")
                            break
                        
                        # リトライ
                        try:
                            if use_oneclick:
                                direction_button = WebDriverWait(self.driver, 1).until(
                                    EC.element_to_be_clickable((By.CSS_SELECTOR, direction_button_selector))
                                )
                                direction_button.click()
                                print(f"  → リトライ: {action_name}ボタンをクリックしました")
                            else:
                                purchase_button = WebDriverWait(self.driver, 1).until(
                                    EC.element_to_be_clickable((By.CSS_SELECTOR, purchase_button_selector))
                                )
                                purchase_button.click()
                                print(f"  → リトライ: 購入ボタンをクリックしました")
                            time.sleep(wait_time * 0.5)
                        except TimeoutException:
                            print(f"  → リトライ: ボタンが見つかりません")
                        
                        # エントリー本数を再確認
                        current_entry_count = self.get_entry_count()
                        if current_entry_count >= expected_count:
                            print(f"  → リトライ成功: エントリー本数が目標に達しました ({current_entry_count}本)")
                            break
                        
                        time.sleep(retry_check_interval)
                    
                    # ループを抜けた後、全体タイムアウトかどうかをチェック
                    total_elapsed = time.time() - trade_start_time
                    if total_elapsed >= trade_retry_seconds:
                        final_count = self.get_entry_count()
                        actual_entries = final_count - base_entry_count
                        print(f"\n  → 全体タイムアウト: 取引を中止します")
                        print(f"  → 完了エントリー: {actual_entries}/{count}本")
                        break
                    
                    print(f"  → {action_name}取引を実行しました ({i+1}/{count})")
                        
                except TimeoutException as e:
                    print(f"エラー: ボタンが見つかりません ({i+1}/{count}): {e}")
                    # 全体タイムアウトをチェック
                    total_elapsed = time.time() - trade_start_time
                    if total_elapsed >= trade_retry_seconds:
                        print(f"  → 全体タイムアウト: 取引を中止します")
                        break
                except Exception as e:
                    print(f"エラー: 取引実行中にエラーが発生しました ({i+1}/{count}): {e}")
                    # 全体タイムアウトをチェック
                    total_elapsed = time.time() - trade_start_time
                    if total_elapsed >= trade_retry_seconds:
                        print(f"  → 全体タイムアウト: 取引を中止します")
                        break
            
            # 最終的なエントリー本数を確認
            final_entry_count = self.get_entry_count()
            actual_count = final_entry_count - base_entry_count
            total_time = time.time() - trade_start_time
            print(f"\n{action_name}取引完了: {actual_count}/{count} 本エントリー成功")
            print(f"  最終エントリー本数: {final_entry_count}本 (基準: {base_entry_count}本)")
            print(f"  処理時間: {total_time:.2f}秒")
            return actual_count > 0
            
        except Exception as e:
            print(f"エラー: 取引実行中にエラーが発生しました: {e}")
            return False
    
    def execute_trade_without_currency_change(self, direction: str, count: int = 1, amount: str = None, trading_time: str = None, retry_seconds: float = None):
        """
        通貨変更をせずに取引を実行（テストモード用）
        
        Args:
            direction: 取引方向 ('buy' または 'sell')
            count: 実行回数（エントリー本数）
            amount: 取引金額（指定しない場合はデフォルト値を使用）
            trading_time: 取引時間（指定しない場合はデフォルト値を使用）
            retry_seconds: リトライ秒数（指定しない場合はデフォルト値を使用）
        """
        if not self.driver:
            print("エラー: ブラウザが起動していません。")
            return False
        
        try:
            # 取引方向に応じてボタンセレクターを選択
            if direction.lower() == 'buy':
                direction_button_selector = self.config['theoption_settings']['buy_button_selector']
                action_name = "買い"
            elif direction.lower() == 'sell':
                direction_button_selector = self.config['theoption_settings']['sell_button_selector']
                action_name = "売り"
            else:
                print(f"エラー: 無効な取引方向 '{direction}' です。")
                return False
            
            # 購入ボタンのセレクター
            purchase_button_selector = self.config['theoption_settings']['purchase_button_selector']
            wait_time = self.config['theoption_settings'].get('wait_time_between_actions', 0.5)
            
            # 設定値の決定（指定されていない場合はデフォルト値を使用）
            trade_amount = amount or self.config['theoption_settings']['default_amount']
            trade_time = trading_time or self.config['theoption_settings']['default_time']
            trade_retry_seconds = retry_seconds if retry_seconds is not None else self.config['theoption_settings'].get('retry_seconds', 10)
            
            # 現在表示されている通貨を取得
            current_currency = self.get_current_currency_from_display()
            trade_currency = current_currency if current_currency else "（表示中の通貨）"
            
            # ボタンセレクターが設定されていない場合の警告
            if not direction_button_selector:
                print(f"警告: {action_name}ボタンのセレクターが設定されていません。")
                print("config.jsonでセレクターを設定してください。")
                return False
            
            if not purchase_button_selector:
                print("警告: 購入ボタンのセレクターが設定されていません。")
                print("config.jsonでpurchase_button_selectorを設定してください。")
                return False
            
            # 購入ボタンが画面上に存在するかチェック
            if not self.is_purchase_button_available():
                print("警告: 購入ボタンが見つかりません。この取引をスキップします。")
                print("  → ワンクリック注文が有効になっている可能性があります。")
                return False
            
            # 初期のエントリー本数を取得
            initial_entry_count = self.get_entry_count()
            target_entry_count = initial_entry_count + count
            
            print(f"取引実行開始: {action_name}方向 x{count}本")
            print(f"  通貨: {trade_currency} (現在表示中の通貨)")
            print(f"  時間: {trade_time}")
            print(f"  金額: {trade_amount}")
            print(f"  リトライ秒数: {trade_retry_seconds}秒")
            print(f"  現在のエントリー本数: {initial_entry_count}本")
            print(f"  目標エントリー本数: {target_entry_count}本")
            
            # 指定回数分取引を実行
            for i in range(count):
                try:
                    print(f"\n取引実行中 ({i+1}/{count}): {action_name}方向")
                    
                    # 1. 通貨選択はスキップ（現在開いている通貨をそのまま使用）
                    print(f"  → 通貨選択をスキップ（現在の通貨を使用）")
                    
                    # 2. 取引時間選択もスキップ（現在開いている時間をそのまま使用）
                    print(f"  → 取引時間選択をスキップ（現在の時間を使用）")
                    
                    # 3. 金額を設定
                    if not self.set_amount(trade_amount):
                        print(f"  → 金額設定に失敗しました ({i+1}/{count})")
                        continue
                    
                    # 設定後の待機
                    time.sleep(wait_time * 0.5)
                    
                    # 4. BUY/SELLボタンをクリック（1本目のみ）
                    if i == 0:
                        direction_button = WebDriverWait(self.driver, 5).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, direction_button_selector))
                        )
                        direction_button.click()
                        print(f"  → {action_name}ボタンをクリックしました")
                        
                        # BUY/SELLボタンクリック後の待機
                        time.sleep(wait_time)
                    else:
                        print(f"  → {action_name}ボタンのクリックをスキップ（2本目以降）")
                    
                    # 5. 購入ボタンをクリック
                    purchase_button = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, purchase_button_selector))
                    )
                    purchase_button.click()
                    print(f"  → 購入ボタンをクリックしました")
                    
                    # 購入後の待機（画面更新を待つ）
                    time.sleep(wait_time)
                    
                    # 6. エントリー本数を確認
                    current_entry_count = self.get_entry_count()
                    expected_count = initial_entry_count + (i + 1)
                    
                    if current_entry_count < expected_count:
                        # エントリー本数が不足している場合、リトライ（購入ボタンのみクリック）
                        print(f"  → エントリー本数が不足しています。リトライします（購入ボタンのみ）...")
                        retry_start_time = time.time()
                        retry_check_interval = 0.1
                        
                        while time.time() - retry_start_time < trade_retry_seconds:
                            # 購入ボタンをクリック
                            try:
                                purchase_button = WebDriverWait(self.driver, 2).until(
                                    EC.element_to_be_clickable((By.CSS_SELECTOR, purchase_button_selector))
                                )
                                purchase_button.click()
                                print(f"  → リトライ: 購入ボタンをクリックしました")
                                time.sleep(wait_time)
                                # 画面反映を待つため750ms追加待機
                                time.sleep(0.75)
                            except TimeoutException:
                                print(f"  → リトライ: 購入ボタンが見つかりません")
                            
                            # エントリー本数を再確認
                            current_entry_count = self.get_entry_count()
                            if current_entry_count >= expected_count:
                                print(f"  → リトライ成功: エントリー本数が目標に達しました ({current_entry_count}本)")
                                break
                            
                            time.sleep(retry_check_interval)
                        else:
                            # タイムアウト
                            final_count = self.get_entry_count()
                            print(f"  → リトライタイムアウト: エントリー本数={final_count}本 (目標={expected_count}本)")
                            print(f"  → 追加のエントリーをスキップします")
                            break
                    
                    print(f"  → {action_name}取引を実行しました ({i+1}/{count})")
                    
                    # 連続取引間の待機
                    if i < count - 1:
                        time.sleep(wait_time)
                        
                except TimeoutException as e:
                    print(f"エラー: ボタンが見つかりません ({i+1}/{count}): {e}")
                    # エラーが発生した場合も、エントリー本数を確認してリトライ
                    current_entry_count = self.get_entry_count()
                    expected_count = initial_entry_count + (i + 1)
                    if current_entry_count < expected_count:
                        print(f"  → エラー発生後のリトライ: 購入ボタンのみクリックします...")
                        retry_start_time = time.time()
                        retry_check_interval = 0.1
                        
                        while time.time() - retry_start_time < trade_retry_seconds:
                            try:
                                purchase_button = WebDriverWait(self.driver, 2).until(
                                    EC.element_to_be_clickable((By.CSS_SELECTOR, purchase_button_selector))
                                )
                                purchase_button.click()
                                print(f"  → リトライ: 購入ボタンをクリックしました")
                                time.sleep(wait_time)
                                # 画面反映を待つため750ms追加待機
                                time.sleep(0.75)
                            except TimeoutException:
                                pass
                            
                            current_entry_count = self.get_entry_count()
                            if current_entry_count >= expected_count:
                                print(f"  → リトライ成功: エントリー本数が目標に達しました ({current_entry_count}本)")
                                break
                            
                            time.sleep(retry_check_interval)
                        else:
                            print(f"  → リトライタイムアウト: 追加のエントリーをスキップします")
                            break
                except Exception as e:
                    print(f"エラー: 取引実行中にエラーが発生しました ({i+1}/{count}): {e}")
                    # エラーが発生した場合も、エントリー本数を確認してリトライ
                    current_entry_count = self.get_entry_count()
                    expected_count = initial_entry_count + (i + 1)
                    if current_entry_count < expected_count:
                        print(f"  → エラー発生後のリトライ: 購入ボタンのみクリックします...")
                        retry_start_time = time.time()
                        retry_check_interval = 0.1
                        
                        while time.time() - retry_start_time < trade_retry_seconds:
                            try:
                                purchase_button = WebDriverWait(self.driver, 2).until(
                                    EC.element_to_be_clickable((By.CSS_SELECTOR, purchase_button_selector))
                                )
                                purchase_button.click()
                                print(f"  → リトライ: 購入ボタンをクリックしました")
                                time.sleep(wait_time)
                                # 画面反映を待つため750ms追加待機
                                time.sleep(0.75)
                            except TimeoutException:
                                pass
                            
                            current_entry_count = self.get_entry_count()
                            if current_entry_count >= expected_count:
                                print(f"  → リトライ成功: エントリー本数が目標に達しました ({current_entry_count}本)")
                                break
                            
                            time.sleep(retry_check_interval)
                        else:
                            print(f"  → リトライタイムアウト: 追加のエントリーをスキップします")
                            break
            
            # 最終的なエントリー本数を確認
            final_entry_count = self.get_entry_count()
            actual_count = final_entry_count - initial_entry_count
            print(f"\n{action_name}取引完了: {actual_count}/{count} 本エントリー成功")
            print(f"  最終エントリー本数: {final_entry_count}本 (初期: {initial_entry_count}本)")
            return actual_count > 0
            
        except Exception as e:
            print(f"エラー: 取引実行中にエラーが発生しました: {e}")
            return False
    
    def schedule_trades(self):
        """設定ファイルから取引をスケジュール"""
        trades = self.config['trading_settings']['trades']
        
        for trade in trades:
            trade_time = trade['time']
            direction = trade['direction']
            count = trade.get('count', 1)
            amount = trade.get('amount', self.config['theoption_settings']['default_amount'])
            trading_time = trade.get('trading_time', self.config['theoption_settings']['default_time'])
            retry_seconds = trade.get('retry_seconds', None)  # トレードごとのリトライ秒数（指定がない場合はNone）
            comment = trade.get('comment', '')
            
            # 時間をパース（HH:MM:SS.fff形式）
            try:
                time_parts = trade_time.split(':')
                hours = int(time_parts[0])
                minutes = int(time_parts[1])
                seconds_parts = time_parts[2].split('.')
                seconds = int(seconds_parts[0])
                microseconds = int(seconds_parts[1]) * 1000 if len(seconds_parts) > 1 else 0
                
                # 今日の日付で時刻を作成
                now = datetime.now()
                target_time = now.replace(
                    hour=hours, 
                    minute=minutes, 
                    second=seconds, 
                    microsecond=microseconds
                )
                
                # 指定時刻が過去の場合は明日に設定
                if target_time <= now:
                    target_time += timedelta(days=1)
                
                self.scheduled_trades.append({
                    'time': target_time,
                    'direction': direction,
                    'count': count,
                    'amount': amount,
                    'trading_time': trading_time,
                    'retry_seconds': retry_seconds,
                    'comment': comment,
                    'original_time': trade_time  # 次の日のスケジュール用に元の時刻を保存
                })
                
                print(f"取引をスケジュールしました: {target_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} - {direction} x{count}")
                print(f"  時間: {trading_time} | 金額: {amount}")
                if comment:
                    print(f"  コメント: {comment}")
                    
            except (ValueError, IndexError) as e:
                print(f"エラー: 時刻形式が正しくありません '{trade_time}': {e}")
    
    def _trade_scheduler_thread(self):
        """取引スケジューラーのスレッド"""
        while self.is_running:
            current_time = datetime.now()
            
            # 毎日朝7時に設定をリセットするかチェック
            self._check_daily_reset()
            
            # 実行すべき取引をチェック
            trades_to_execute = []
            remaining_trades = []
            
            for trade in self.scheduled_trades:
                if current_time >= trade['time']:
                    trades_to_execute.append(trade)
                else:
                    remaining_trades.append(trade)
            
            # 実行すべき取引を処理
            for trade in trades_to_execute:
                print(f"\n=== 取引実行 ===")
                print(f"時刻: {trade['time'].strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
                print(f"方向: {trade['direction']}")
                print(f"回数: {trade['count']}")
                print(f"時間: {trade['trading_time']}")
                print(f"金額: {trade['amount']}")
                if trade.get('retry_seconds') is not None:
                    print(f"リトライ秒数: {trade['retry_seconds']}秒")
                if trade['comment']:
                    print(f"コメント: {trade['comment']}")
                
                self.execute_trade(
                    trade['direction'], 
                    trade['count'], 
                    trade['amount'],
                    trade['trading_time'],
                    trade.get('retry_seconds'),
                    reset_entry_count=True  # スケジュール実行時はエントリー本数をリセット
                )
                
                # 次の日の同じ時刻にスケジュールを追加
                original_time = trade.get('original_time', trade['time'].strftime('%H:%M:%S.%f')[:-3])
                try:
                    time_parts = original_time.split(':')
                    hours = int(time_parts[0])
                    minutes = int(time_parts[1])
                    seconds_parts = time_parts[2].split('.')
                    seconds = int(seconds_parts[0])
                    microseconds = int(seconds_parts[1]) * 1000 if len(seconds_parts) > 1 else 0
                    
                    # 明日の同じ時刻を作成
                    now = datetime.now()
                    next_day_time = (now + timedelta(days=1)).replace(
                        hour=hours,
                        minute=minutes,
                        second=seconds,
                        microsecond=microseconds
                    )
                    
                    # 次の日の取引をスケジュールに追加
                    remaining_trades.append({
                        'time': next_day_time,
                        'direction': trade['direction'],
                        'count': trade['count'],
                        'amount': trade['amount'],
                        'trading_time': trade['trading_time'],
                        'retry_seconds': trade.get('retry_seconds'),
                        'comment': trade['comment'],
                        'original_time': original_time
                    })
                    
                    print(f"  → 次の日の取引をスケジュールしました: {next_day_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
                except (ValueError, IndexError) as e:
                    print(f"警告: 次の日のスケジュール追加に失敗しました: {e}")
            
            # 残りの取引を更新
            self.scheduled_trades = remaining_trades
            
            # 1ms間隔でチェック
            time.sleep(0.001)
    
    def start_trading(self):
        """自動売買を開始"""
        if not self.driver:
            print("エラー: ブラウザが起動していません。先にstart_browser()を実行してください。")
            return
        
        if not self.scheduled_trades:
            print("エラー: スケジュールされた取引がありません。")
            return
        
        print("\n=== 自動売買開始 ===")
        print(f"スケジュールされた取引数: {len(self.scheduled_trades)}")
        
        # 前回のエントリー本数キャッシュをクリアしてリロード
        print("\n前回のキャッシュをクリアしています...")
        self.clear_browser_cache_and_reload()
        
        self.is_running = True
        
        # スケジューラーをバックグラウンドで実行
        scheduler_thread = threading.Thread(target=self._trade_scheduler_thread)
        scheduler_thread.daemon = True
        scheduler_thread.start()
        
        try:
            # メインスレッドで待機
            while self.is_running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\n\n取引を中断しています...")
            self.is_running = False
            scheduler_thread.join(timeout=1)
            print("自動売買を終了しました。")
            # CTRL+Cで終了する場合は、ここでreturnしてブラウザを閉じない
            return
        
        # 通常終了の場合（is_runningがFalseになった場合）
        print("自動売買を終了しました。")
    
    def stop_trading(self):
        """自動売買を停止"""
        self.is_running = False
        print("自動売買を停止しました。")
    
    def execute_random_trade(self):
        """ランダムな取引を実行（テストモード用）"""
        if not self.driver:
            print("エラー: ブラウザが起動していません。")
            return False
        
        test_settings = self.config.get('test_mode_settings', {})
        if not test_settings.get('enabled', False):
            print("テストモードが無効になっています。")
            return False
        
        # ランダムな方向を選択
        directions = test_settings.get('directions', ['buy', 'sell'])
        random_direction = random.choice(directions)
        
        # ランダムな金額を選択
        amounts = test_settings.get('random_amounts', ['1000'])
        random_amount = random.choice(amounts)
        
        # 現在開いている通貨を取得
        current_currency = self.get_current_currency()
        trade_currency = current_currency if current_currency else "（表示中の通貨）"
        
        # 現在開いている取引時間を取得、取得できない場合はデフォルト時間を使用
        current_time = self.get_current_trading_time()
        trade_time = current_time if current_time else self.config['theoption_settings']['default_time']
        
        print(f"\n=== テストモード: ランダム取引実行 ===")
        print(f"方向: {random_direction}")
        print(f"通貨: {trade_currency}" + (" (現在表示中の通貨)" if current_currency else ""))
        print(f"時間: {trade_time}" + (" (現在開いている時間)" if current_time else " (デフォルト時間)"))
        print(f"金額: {random_amount}")
        
        # 取引を実行（通貨・時間選択をスキップして現在の設定で実行）
        return self.execute_trade_without_currency_change(random_direction, 1, random_amount, trade_time)
    
    def start_test_mode(self):
        """テストモードを開始"""
        if not self.driver:
            print("エラー: ブラウザが起動していません。先にstart_browser()を実行してください。")
            return
        
        test_settings = self.config.get('test_mode_settings', {})
        if not test_settings.get('enabled', False):
            print("テストモードが無効になっています。config.jsonで有効にしてください。")
            return
        
        print("\n=== テストモード開始 ===")
        print("Enterキーを押すとランダムな取引を実行します")
        print("'q' + Enterで終了します")
        print(f"設定可能な方向: {test_settings.get('directions', ['buy', 'sell'])}")
        print(f"設定可能な金額: {test_settings.get('random_amounts', ['1000'])}")
        print("-" * 50)
        
        while True:
            try:
                user_input = input("\nEnterキーを押してランダム取引実行 (qで終了): ").strip().lower()
                
                if user_input == 'q':
                    print("テストモードを終了します。")
                    break
                elif user_input == '':
                    # Enterキーが押された場合
                    self.execute_random_trade()
                else:
                    print("無効な入力です。Enterキーまたは'q'を入力してください。")
                    
            except KeyboardInterrupt:
                print("\n\nテストモードを中断します。")
                break
            except Exception as e:
                print(f"エラーが発生しました: {e}")
                continue
    
    def show_scheduled_trades(self):
        """スケジュールされた取引を表示"""
        if not self.scheduled_trades:
            print("スケジュールされた取引はありません。")
            return
        
        print(f"\n=== スケジュールされた取引一覧 ({len(self.scheduled_trades)}件) ===")
        for i, trade in enumerate(self.scheduled_trades, 1):
            print(f"{i}. {trade['time'].strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} - {trade['direction']} x{trade['count']}")
            print(f"   時間: {trade['trading_time']} | 金額: {trade['amount']}")
            if trade['comment']:
                print(f"   コメント: {trade['comment']}")
    
    def show_error_logs(self, lines: int = 50):
        """
        エラーログを表示
        
        Args:
            lines: 表示する行数（デフォルト: 50）
        """
        log_file = os.path.join("logs", "theoption_trader.log")
        
        if not os.path.exists(log_file):
            print("エラーログファイルが見つかりません。")
            return
        
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                all_lines = f.readlines()
                
            # 最後の指定行数を取得
            recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
            
            print(f"\n=== エラーログ（最新{len(recent_lines)}行） ===")
            for line in recent_lines:
                print(line.rstrip())
            print("=" * 50)
            
        except Exception as e:
            print(f"エラーログの読み込みに失敗しました: {e}")
    
    def show_error_summary(self):
        """エラーの概要を表示"""
        log_file = os.path.join("logs", "theoption_trader.log")
        
        if not os.path.exists(log_file):
            print("エラーログファイルが見つかりません。")
            return
        
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # エラータイプ別の集計
            error_counts = {}
            windows32_errors = []
            chrome_errors = []
            selenium_errors = []
            trading_errors = []
            
            for line in lines:
                if 'ERROR' in line:
                    if '[Windows32]' in line:
                        windows32_errors.append(line.strip())
                        error_counts['Windows32'] = error_counts.get('Windows32', 0) + 1
                    elif '[ChromeDriver]' in line:
                        chrome_errors.append(line.strip())
                        error_counts['ChromeDriver'] = error_counts.get('ChromeDriver', 0) + 1
                    elif '[Selenium]' in line:
                        selenium_errors.append(line.strip())
                        error_counts['Selenium'] = error_counts.get('Selenium', 0) + 1
                    elif '[Trading]' in line:
                        trading_errors.append(line.strip())
                        error_counts['Trading'] = error_counts.get('Trading', 0) + 1
                    elif '[ConfigFile]' in line:
                        error_counts['ConfigFile'] = error_counts.get('ConfigFile', 0) + 1
            
            print(f"\n=== エラー概要 ===")
            print(f"総エラー数: {sum(error_counts.values())}")
            print("\nエラータイプ別:")
            for error_type, count in error_counts.items():
                print(f"  {error_type}: {count}件")
            
            if windows32_errors:
                print(f"\n=== Windows32エラー（最新5件） ===")
                for error in windows32_errors[-5:]:
                    print(f"  {error}")
            
            if chrome_errors:
                print(f"\n=== ChromeDriverエラー（最新5件） ===")
                for error in chrome_errors[-5:]:
                    print(f"  {error}")
            
            if selenium_errors:
                print(f"\n=== Seleniumエラー（最新5件） ===")
                for error in selenium_errors[-5:]:
                    print(f"  {error}")
            
            if trading_errors:
                print(f"\n=== 取引エラー（最新5件） ===")
                for error in trading_errors[-5:]:
                    print(f"  {error}")
            
            print("=" * 50)
            
        except Exception as e:
            print(f"エラー概要の取得に失敗しました: {e}")


def main():
    """メイン関数"""
    print("=== TheOption自動売買ツール ===")
    
    # 設定ファイルの存在確認
    config_path = "config.json"
    if not os.path.exists(config_path):
        print(f"エラー: 設定ファイル '{config_path}' が見つかりません。")
        return
    
    # トレーダーインスタンスを作成
    trader = TheOptionTrader(config_path)
    
    try:
        # ブラウザを起動
        trader.start_browser()
        
        # 取引をスケジュール
        trader.schedule_trades()
        
        # スケジュールされた取引を表示
        trader.show_scheduled_trades()
        
        # モード選択
        print("\n=== モード選択 ===")
        print("1. 自動売買モード（スケジュールされた取引を実行）")
        print("2. テストモード（手動でランダム取引を実行）")
        print("3. エラーログ表示")
        print("4. 終了")
        
        while True:
            try:
                choice = input("\nモードを選択してください (1/2/3/4): ").strip()
                
                if choice == '1':
                    if trader.scheduled_trades:
                        print("\n自動売買を開始しますか？ (y/N): ", end="")
                        response = input().strip().lower()
                        
                        if response in ['y', 'yes']:
                            trader.start_trading()
                        else:
                            print("自動売買をキャンセルしました。")
                    else:
                        print("スケジュールされた取引がありません。")
                    break
                    
                elif choice == '2':
                    trader.start_test_mode()
                    break
                    
                elif choice == '3':
                    print("\n=== エラーログ表示 ===")
                    print("1. エラー概要を表示")
                    print("2. 最新のエラーログを表示")
                    print("3. 戻る")
                    
                    log_choice = input("選択してください (1/2/3): ").strip()
                    
                    if log_choice == '1':
                        trader.show_error_summary()
                    elif log_choice == '2':
                        lines = input("表示する行数を入力してください（デフォルト: 50）: ").strip()
                        try:
                            lines = int(lines) if lines else 50
                        except ValueError:
                            lines = 50
                        trader.show_error_logs(lines)
                    elif log_choice == '3':
                        continue
                    else:
                        print("無効な選択です。")
                    
                elif choice == '4':
                    print("プログラムを終了します。")
                    break
                    
                else:
                    print("無効な選択です。1、2、3、または4を入力してください。")
                    
            except KeyboardInterrupt:
                print("\n\nプログラムを中断します。")
                break
        
    except KeyboardInterrupt:
        print("\n\nプログラムを中断しています...")
        print("ブラウザは開いたままです。必要に応じて手動で閉じてください。")
    except Exception as e:
        print(f"エラーが発生しました: {e}")
        print("ブラウザは開いたままです。必要に応じて手動で閉じてください。")
    # finallyブロックを削除して、ブラウザを閉じないようにする


if __name__ == "__main__":
    main()

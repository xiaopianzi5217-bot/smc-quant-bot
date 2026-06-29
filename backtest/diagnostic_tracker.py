import os
import datetime
import pandas as pd
from collections import defaultdict

class DiagnosticTracker:
    def __init__(self, version_name="V31机构评分版"):
        self.version_name = version_name
        self.total_klines = 0
        
        # 每次回测开始时打印分隔线，标记新的回测会话
        print(f"\n{'='*60}")
        print(f"🔄 新回测会话开始 | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"🧬 Version: {self.version_name}")
        print(f"{'='*60}\n")
        
        # 漏斗统计 (Engine Diagnostic)
        self.funnel = {
            "divergence": {"bull": 0, "bear": 0},
            "squeeze": 0,
            "breakout": {"high": 0, "low": 0},
            "volume_z": 0,
            "atr_expand": 0,
            "sqz_dmi": {"long": 0, "short": 0},
            "combo_trigger": {"long": 0, "short": 0},
            "htf_conflict": {"long_against_1h_short": 0, "short_against_1h_long": 0}
        }
        
        # 阵亡原因统计 (X光排查报告)
        self.reject_reasons = defaultdict(int)
        
        # 存放被拒绝的明细，用来导出 reject_audit.csv
        self.reject_audit_list = []
        
        # 分数历史（每次回测开始时清空）
        self.score_history = []

    def log_reject(self, timestamp, reason, score=0, details=""):
        """记录信号阵亡原因"""
        self.reject_reasons[reason] += 1
        self.reject_audit_list.append({
            "timestamp": timestamp,
            "reject_reason": reason,
            "score": score,
            "details": details
        })

    def print_final_report(self):
        """打印最终的酷炫日志"""
        print(f"\n🧬 Runner Version: {self.version_name}")
        print(f"=======================================================")
        print(f"🕵️‍♂️ [Breakout Engine Diagnostic / 突破引擎漏斗分析]")
        print(f" K线总数: {self.total_klines}")
        print(f" 1. 近期背离数 (Bull/Bear): {self.funnel['divergence']['bull']} / {self.funnel['divergence']['bear']}")
        print(f" 2. 达到横盘挤压条件 (>= 8根): {self.funnel['squeeze']}")
        print(f" 3. 价格突破20前高/前低数: {self.funnel['breakout']['high']} / {self.funnel['breakout']['low']}")
        print(f" 4. 量能爆发满足数 (Vol Z > 1.15): {self.funnel['volume_z']}")
        print(f" 5. 波动扩张满足数 (ATR > 1.05): {self.funnel['atr_expand']}")
        print(f" 6. SQZMOM Strength+DMI满足数: {self.funnel['sqz_dmi']['long']} / {self.funnel['sqz_dmi']['short']}")
        print(f" => 最终组合成功触发 (Long/Short): {self.funnel['combo_trigger']['long']} / {self.funnel['combo_trigger']['short']}")
        print(f"=======================================================")
        
        print(f"\n=======================================================")
        print(f"⚠️ 实际参与回测的有效 K 线数量：{self.total_klines} 根")
        print(f"=======================================================")
        
        print(f"\n🔍 【X光排查报告】 信号阵亡原因统计：")
        # 按照你想要的格式输出
        for reason, count in sorted(self.reject_reasons.items()):
            print(f" ❌ {reason}: {count} 次")
        print(f"=======================================================")
        
    # ──────────────────────────────
    # 🌟 新增：兼容 V31 引擎的批量拒绝记录方法
    # ──────────────────────────────
    def log_reject_bulk(self, timestamp, reason, bucket="", row=None, direction=None, exec_ctx=None, score=0.0, details=None):
        """兼容批量或单条拒绝信号的日志记录接口。

        修复点：旧版调用只把 direction 放在 details 里，导致导出的顶层
        direction 列全为空。这里做一次回填，并把常用字段稳定输出。
        """
        self.reject_reasons[reason] += 1

        if direction is None and isinstance(details, dict):
            direction = details.get("direction")
        if bucket == "" and isinstance(details, dict):
            bucket = details.get("bucket", "")

        audit_entry = {
            "timestamp": timestamp,
            "reason": reason,
            "bucket": bucket,
            "score": score,
            "direction": direction,
        }

        if isinstance(details, dict):
            for k, v in details.items():
                audit_entry[f"meta_{k}"] = v
        elif details is not None:
            audit_entry["details"] = str(details)

        self.reject_audit_list.append(audit_entry)

    def adjust(self, score: float) -> float:
        """诊断调整：只做日志记录，不修改分数。
        
        用法:
            score = adaptive_signal_score(ctx, direction)
            print("AFTER adaptive:", score)
            print("AFTER diagnostic:", tracker.adjust(score))
        """
        # 记录分数分布（可用于后续分析）
        if not hasattr(self, "score_history"):
            self.score_history = []
        self.score_history.append(score)
        
        # 打印诊断信息
        print(f"  🔍 [DiagnosticTracker] score={score:.2f}")
        
        # 原样返回分数，不做任何修改
        return score

    def export_reject_audit(self, filename: str = "reject_audit_v30.csv") -> str:
        """終極修復版：自動建立資料夾，防止因路徑不存在導致檔案消失"""
        if not self.reject_audit_list:
            print("📄 [警告] 無任何拒絕記錄，跳過導出。")
            return ""

        try:
            # 1. 提取資料夾路徑（例如：如果傳入 "data/audit.csv"，則提取出 "data"）
            dir_name = os.path.dirname(filename)
            
            # 2. 如果有指定資料夾，且資料夾不存在，則強行自動建立
            if dir_name and not os.path.exists(dir_name):
                os.makedirs(dir_name, exist_ok=True)
                print(f"📁 系統自動建立了缺失的資料夾: {dir_name}")

            # 3. 為了防止多線程熱載入覆蓋，給檔名自動加上時間戳記
            base_name, ext = os.path.splitext(os.path.basename(filename))
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            actual_filename = f"{base_name}_{timestamp}{ext}"
            
            # 如果有資料夾路徑，重新拼接
            if dir_name:
                actual_filename = os.path.join(dir_name, actual_filename)

            # 4. 轉為絕對路徑，強行寫入 Hf Spaces / 虛擬機磁碟
            abs_path = os.path.abspath(actual_filename)
            
            df = pd.DataFrame(self.reject_audit_list)
            df.to_csv(abs_path, index=False)
            
            print(f"📄 【🎯 審計檔案成功封鎖】")
            print(f"   ↳ 實際儲存絕對路徑: {abs_path}")
            print(f"   ↳ 總計成功寫入訊號: {len(df)} 行")
            
            return abs_path

        except Exception as e:
            print(f"❌ 導出 CSV 時發生嚴重錯誤: {str(e)}")
            return ""

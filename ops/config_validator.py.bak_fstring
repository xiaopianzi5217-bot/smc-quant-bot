# -*- coding: utf-8 -*-

class ConfigValidator:
    REQUIRED = ["mode", "risk_pct", "max_positions", "max_daily_loss_usdt"]

    def validate(self, config):
        issues = []
        for k in self.REQUIRED:
            if k not in config:
                issues.append(f"缺少配置: {k}")
        if float(config.get("risk_pct", 0)) <= 0:
            issues.append("risk_pct 必须大于0")
        if float(config.get("risk_pct", 0)) > 0.03:
            issues.append("risk_pct 过高，建议不超过 3%")
        if int(config.get("max_positions", 0)) <= 0:
            issues.append("max_positions 必须大于0")
        return len(issues) == 0, issues

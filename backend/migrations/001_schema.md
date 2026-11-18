# 初始结构

SQLAlchemy启动创建以下业务表：service_periods, service_cases, business_calendars, sla_runs, delivery_schedules, deliveries、users、audits。Source专属JSON字段由对应validation严格校验；Policy为不可覆盖的版本记录；Run保存source/policy冻结快照；Period仅用于已有期间选择器，不计入核心复杂度。已有数据库升级需先备份，后续字段演进应以独立迁移执行。

# 002 两地日历组合

新增 calendar_pairs：id、name（组合名）、department（绑定小组，加索引）、version（按小组递增，与department唯一约束）、epoch（规则纪元镜像，预计算失效守卫的可观测副本）、data（home/away两地日历与sla_minutes的JSON）、created。两地核算报告仍在 sla_runs.snapshot 内冻结 pairs 与 sources（snapshot含pairs键即两地口径），历史单地报告结构不变，按原规则读取。预计算分钟网格存Redis（pair:pre:{小组}:{规则散列}，TTL 24h），纪元在 pair:epoch:{小组}，重建任务走 pair-jobs 队列。

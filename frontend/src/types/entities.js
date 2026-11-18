// API实体字段约定：来源{id,natural_key,data}、口径{id,version,data}、日历组合{id,name,department,version,epoch,data,precompute}、报告{id,status,snapshot,checksum,result,approved}、计划{next_at,enabled}。
export const ENTITY_KEYS=['sources','policies','pairs','runs','schedules','deliveries'];

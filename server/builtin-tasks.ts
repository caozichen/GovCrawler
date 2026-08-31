import { getDefaultScriptDetails } from './script-details.js'
import type { CrawlerTask } from './types.js'

// Increment this when adding catalog entries so existing installations import them once.
export const BUILTIN_CRAWLER_CATALOG_VERSION = 1

export interface BuiltinCrawlerDefinition {
  id: string
  categoryId: 'municipal' | 'district' | 'agency'
  name: string
  url: string
  scriptPath: `crawlers/${string}.py`
  timeoutSeconds: number
}

export const BUILTIN_CRAWLER_DEFINITIONS = Object.freeze([
  {
    id: 'builtin-beijing-hr-recruitment',
    categoryId: 'municipal',
    name: '北京市事业单位公开招聘',
    url: 'https://rsj.beijing.gov.cn/xxgk/gkzp/',
    scriptPath: 'crawlers/beijing_hr_recruitment.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-beijing-economy-information-recruitment',
    categoryId: 'municipal',
    name: '北京市经济和信息化局招聘公告',
    url: 'https://jxj.beijing.gov.cn/zwgk/rsxx/',
    scriptPath: 'crawlers/beijing_economy_information_recruitment_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-beijing-culture-tourism-recruitment',
    categoryId: 'municipal',
    name: '北京市文物局招聘公告',
    url: 'https://wwj.beijing.gov.cn/bjww/362690/362733/',
    scriptPath: 'crawlers/beijing_culture_tourism_recruitment_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-beijing-intellectual-property-recruitment',
    categoryId: 'municipal',
    name: '北京市知识产权局招聘公告',
    url: 'https://zscqj.beijing.gov.cn/zscqj/zwgk/tzgg/',
    scriptPath: 'crawlers/beijing_intellectual_property_recruitment_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-beijing-planning-natural-resources-recruitment',
    categoryId: 'municipal',
    name: '北京市规划和自然资源委员会招聘公告',
    url: 'https://ghzrzyw.beijing.gov.cn/zhengwuxinxi/tzgg/',
    scriptPath: 'crawlers/beijing_planning_natural_resources_recruitment_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-beijing-statistics-recruitment',
    categoryId: 'municipal',
    name: '北京市统计局招聘公告',
    url: 'https://tjj.beijing.gov.cn/zwgkai/zkly_31385/',
    scriptPath: 'crawlers/beijing_statistics_recruitment_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-beijing-market-regulation-recruitment',
    categoryId: 'municipal',
    name: '北京市市场监督管理局招聘公告',
    url: 'https://scjgj.beijing.gov.cn/zwxx/rsxx/',
    scriptPath: 'crawlers/beijing_market_regulation_recruitment_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-beijing-landscaping-recruitment',
    categoryId: 'municipal',
    name: '北京市园林绿化局招聘公告',
    url: 'https://yllhj.beijing.gov.cn/zwgk/zkzp/',
    scriptPath: 'crawlers/beijing_landscaping_recruitment_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-beijing-ecology-environment-recruitment',
    categoryId: 'municipal',
    name: '北京市生态环境局招聘公告',
    url: 'https://sthjj.beijing.gov.cn/bjhrb/index/xxgk69/zfxxgk43/fdzdgknr2/zkzl/',
    scriptPath: 'crawlers/beijing_ecology_environment_recruitment_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-beijing-sports-recruitment',
    categoryId: 'municipal',
    name: '北京市体育局招聘公告',
    url: 'https://tyj.beijing.gov.cn/bjsports/zfxxgk_/gkzp/',
    scriptPath: 'crawlers/beijing_sports_recruitment_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-beijing-civil-affairs-recruitment',
    categoryId: 'municipal',
    name: '北京市民政局招聘公告',
    url: 'https://mzj.beijing.gov.cn/col/col383/index.html',
    scriptPath: 'crawlers/beijing_civil_affairs_recruitment_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-beijing-transport-recruitment',
    categoryId: 'municipal',
    name: '北京市交通委员会招聘公告',
    url: 'https://jtw.beijing.gov.cn/xxgk/rykl1/',
    scriptPath: 'crawlers/beijing_transport_recruitment_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-beijing-chaoyang-recruitment',
    categoryId: 'district',
    name: '北京市朝阳区公开招聘（最近 10 条）',
    url: 'http://www.bjchy.gov.cn/affair/civilemployed/zhk/',
    scriptPath: 'crawlers/beijing_chaoyang_recruitment.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-dongcheng-public-institutions',
    categoryId: 'district',
    name: '北京市东城区事业单位招聘（最近 10 条）',
    url: 'https://www.bjdch.gov.cn/zwgk/rsxx/sydwzlxx/',
    scriptPath: 'crawlers/dongcheng_public_institutions.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-xicheng-recruitment',
    categoryId: 'district',
    name: '北京市西城区招聘公告',
    url: 'https://www.bjxch.gov.cn/xxgk/tzgg.html',
    scriptPath: 'crawlers/xicheng_recruitment_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-tongzhou-recruitment',
    categoryId: 'district',
    name: '北京市通州区招聘公告',
    url: 'https://www.bjtzh.gov.cn/bjtz/zwgk/tzgg/index.shtml',
    scriptPath: 'crawlers/tongzhou_recruitment_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-daxing-recruitment',
    categoryId: 'district',
    name: '北京市大兴区招聘公告',
    url: 'https://www.bjdx.gov.cn/bjsdxqrmzf/zwfw/rsxx/klzp/index.html',
    scriptPath: 'crawlers/daxing_recruitment_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-haidian-recruitment',
    categoryId: 'district',
    name: '北京市海淀区招聘公告',
    url: 'https://zyk.bjhd.gov.cn/jbdt/auto4496_51791/',
    scriptPath: 'crawlers/haidian_recruitment_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-beijing-etda-recruitment',
    categoryId: 'district',
    name: '北京经济技术开发区招聘公告',
    url: 'https://kfqgw.beijing.gov.cn/zwgkkfq/tzgg/',
    scriptPath: 'crawlers/beijing_etda_recruitment_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-beijing-portal-recruitment',
    categoryId: 'agency',
    name: '北京市人民政府门户网站招聘公告',
    url: 'https://www.beijing.gov.cn/fwcj/quickQuery/page/zhaopin/',
    scriptPath: 'crawlers/beijing_portal_recruitment_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-beijing-government-public-institution',
    categoryId: 'agency',
    name: '首都之窗事业单位招聘公告',
    url: 'https://www.beijing.gov.cn/gongkai/rsxx/sydwzp/',
    scriptPath: 'crawlers/beijing_government_public_institution_announcements.py',
    timeoutSeconds: 600,
  },
  {
    id: 'builtin-example',
    categoryId: 'agency',
    name: '示例网页采集',
    url: 'https://example.com',
    scriptPath: 'crawlers/example.py',
    timeoutSeconds: 120,
  },
] satisfies readonly BuiltinCrawlerDefinition[])

export function createBuiltinTask(
  definition: BuiltinCrawlerDefinition,
  now: string,
  categoryId: string = definition.categoryId,
): CrawlerTask {
  return {
    id: definition.id,
    builtinId: definition.id,
    categoryId,
    name: definition.name,
    url: definition.url,
    scriptPath: definition.scriptPath,
    details: getDefaultScriptDetails(definition.scriptPath),
    pythonPath: '',
    enabled: false,
    intervalMinutes: 30,
    timeoutSeconds: definition.timeoutSeconds,
    extraArgs: [],
    createdAt: now,
    updatedAt: now,
  }
}

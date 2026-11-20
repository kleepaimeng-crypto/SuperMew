<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";
import { useEvaluationsStore } from "../stores/evaluations";
import { label, active } from "../evaluations";
const store = useEvaluationsStore(),
  auth = useAuthStore(),
  route = useRoute(),
  router = useRouter();
const corpus = ref(""),
  dataset = ref<unknown>(null),
  scenarios = ref<unknown>(null),
  datasetName = ref(""),
  scenarioName = ref(""),
  fileError = ref("");
const baseline = ref(""),
  changes = ref<string[]>([]),
  limit = ref(112),
  caseSeconds = ref(120),
  jobSeconds = ref(1800);
const uploadKey = ref(0);
const jobId = computed(() =>
  typeof route.params.jobId === "string" ? route.params.jobId : undefined,
);
const ready = computed(
  () =>
    !!corpus.value && dataset.value !== null && Array.isArray(scenarios.value),
);
const body = () => ({
  corpus_id: corpus.value,
  dataset: dataset.value,
  scenarios: scenarios.value,
  total_model_limit: limit.value,
  case_seconds: caseSeconds.value,
  job_seconds: jobSeconds.value,
});
const options = computed(() =>
  store.jobs.filter(
    (j) => j.status === "completed" && j.job_id !== jobId.value,
  ),
);
const currentReport = computed(() => store.comparison ?? store.report);
watch([baseline, changes], () => store.clearComparison(), { deep: true });
const metrics: Record<string, string> = {
  material_coverage: "材料覆盖",
  outcome_match: "结果符合预期",
  citation_structure: "引用结构",
  budget_ok: "预算符合",
  semantic_correctness: "结构层语义指标",
};
const variables: Record<string, string> = {
  strategy: "RAG策略",
  model: "模型",
  implementation: "实现",
  dependencies: "依赖",
  index: "索引",
};
function clearInputs() {
  dataset.value = scenarios.value = null;
  datasetName.value =
    scenarioName.value =
    corpus.value =
    baseline.value =
    fileError.value =
      "";
  changes.value = [];
  uploadKey.value++;
}
watch(() => auth.user?.id, clearInputs, { flush: "sync" });
watch([corpus, dataset, scenarios, limit, caseSeconds, jobSeconds], () => {
  store.checked = false;
});
watch(
  jobId,
  (id) => {
    baseline.value = "";
    changes.value = [];
    void store.select(id);
  },
  { immediate: true },
);
void store.load("jobs");
void store.load("corpora");
onBeforeUnmount(() => store.reset());
let fileOrder = { dataset: 0, scenario: 0 };
async function choose(event: Event, kind: "dataset" | "scenario") {
  const file = (event.target as HTMLInputElement).files?.[0];
  const order = ++fileOrder[kind],
    owner = auth.user?.id;
  if (kind === "dataset") {
    dataset.value = null;
    datasetName.value = "";
  } else {
    scenarios.value = null;
    scenarioName.value = "";
  }
  fileError.value = "";
  if (!file) return;
  try {
    if (file.size > 2_000_000) throw new Error();
    const parsed: unknown = JSON.parse(await file.text());
    if (owner !== auth.user?.id || order !== fileOrder[kind]) return;
    if (kind === "dataset") {
      dataset.value = parsed;
      datasetName.value = file.name;
    } else {
      if (!Array.isArray(parsed)) throw new Error();
      scenarios.value = parsed;
      scenarioName.value = file.name;
    }
  } catch {
    if (owner === auth.user?.id && order === fileOrder[kind])
      fileError.value =
        "文件须为有效 JSON；Scenario 须为逐题脚本数组，每个文件不超过 2 MB。";
  }
}
async function create() {
  const id = await store.create(body());
  if (id) await router.push(`/evaluations/${id}`);
}
async function fresh() {
  if (store.busy) return;
  store.pending = null;
  store.checked = false;
  clearInputs();
  await router.push("/evaluations");
}
const date = (v: string) => new Date(v).toLocaleString("zh-CN");
</script>
<template>
  <section class="evaluation-workspace" aria-label="评测工作台">
    <aside class="evaluation-directory">
      <div class="evaluation-heading">
        <h1>评测</h1>
        <button
          class="quiet-button"
          :disabled="store.loading"
          @click="store.load('jobs')"
        >
          刷新历史
        </button>
      </div>
      <button class="primary" :disabled="store.busy" @click="fresh">
        新建实验
      </button>
      <p class="evaluation-note">
        全局评测工作台 · 真实本地检索 · 受控生成与评分（不随左侧项目切换范围）
      </p>
      <nav aria-label="实验历史" class="evaluation-history">
        <RouterLink
          v-for="item in store.jobs"
          :key="item.job_id"
          :to="`/evaluations/${item.job_id}`"
          :aria-current="jobId === item.job_id ? 'page' : undefined"
        >
          <strong>{{ item.dataset_id }}</strong
          ><span
            >{{ label(item.status)
            }}<template v-if="item.decision">
              · {{ label(item.decision) }}</template
            ></span
          ><small>{{ date(item.created_at) }}</small>
        </RouterLink>
      </nav>
      <p v-if="!store.jobs.length && !store.loading">还没有评测实验。</p>
      <button
        v-if="store.nextJobs !== null"
        :disabled="store.loading"
        @click="store.load('jobs', true)"
      >
        更多历史
      </button>
    </aside>
    <div class="evaluation-content">
      <div v-if="store.error" class="evaluation-alert" role="alert">
        <p>{{ store.error }}</p>
        <button
          :disabled="store.busy"
          @click="jobId ? store.select(jobId) : store.load('corpora')"
        >
          重新读取
        </button>
      </div>
      <template v-if="!jobId">
        <header>
          <h2>用固定案例检查 RAG</h2>
          <p>选择已准备的资料，运行相同问题，再检查回答与预期的差距。</p>
        </header>
        <form @submit.prevent="create">
          <fieldset :disabled="store.busy || !!store.pending">
            <legend>1 · 选择语料</legend>
            <label for="corpus">已准备的评测资料</label>
            <select id="corpus" v-model="corpus" required>
              <option value="" disabled>请选择语料</option>
              <option
                v-for="item in store.corpora"
                :key="item.corpus_id"
                :value="item.corpus_id"
              >
                {{ item.source_ids.join("、") }} ·
                {{ item.corpus_id.slice(0, 8) }} · 有效至
                {{ date(item.expires_at) }}
              </option>
            </select>
            <p v-if="!store.corpora.length" class="evaluation-note">
              暂无可用资料。请先通过 CLI 准备评测语料，再刷新目录。
            </p>
            <button
              type="button"
              class="quiet-button"
              @click="store.load('corpora')"
            >
              刷新语料
            </button>
            <button
              v-if="store.nextCorpora !== null"
              type="button"
              @click="store.load('corpora', true)"
            >
              更多语料
            </button>
          </fieldset>
          <fieldset :key="uploadKey" :disabled="store.busy || !!store.pending">
            <legend>2 · 导入案例</legend>
            <label for="dataset">Dataset · 题目与人工评分标准</label
            ><input
              id="dataset"
              type="file"
              accept=".json,application/json"
              @change="choose($event, 'dataset')"
            />
            <p v-if="datasetName" class="evaluation-note">
              已读取 {{ datasetName }}
            </p>
            <label for="scenario">Scenario · 受控输出脚本</label
            ><input
              id="scenario"
              type="file"
              accept=".json,application/json"
              @change="choose($event, 'scenario')"
            />
            <p v-if="scenarioName" class="evaluation-note">
              已读取 {{ scenarioName }}
            </p>
            <p>脚本分别定义策略、回答与评分输出；人工标准只进入评分侧。</p>
            <p v-if="fileError" class="error" role="alert">{{ fileError }}</p>
            <details>
              <summary>受控实验能验证什么？</summary>
              <p>
                验证真实检索、执行恢复和报告流程。答案和评分输出来自脚本，通过不代表真实模型的语义质量。引用仍须通过正式答案校验。
              </p>
            </details>
          </fieldset>
          <fieldset :disabled="store.busy || !!store.pending">
            <legend>3 · 确认执行预算</legend>
            <div class="evaluation-budgets">
              <label
                >总模型尝试上限<input
                  v-model.number="limit"
                  type="number"
                  min="1"
                  max="10000"
                  required
              /></label>
              <label
                >每题执行预算（秒）<input
                  v-model.number="caseSeconds"
                  type="number"
                  min="10"
                  max="600"
                  required
              /></label>
              <label
                >实验总期限（秒）<input
                  v-model.number="jobSeconds"
                  type="number"
                  min="30"
                  max="7200"
                  required
              /></label>
            </div>
          </fieldset>
          <div class="evaluation-actions">
            <button
              type="button"
              :disabled="!ready || store.busy || !!store.pending"
              @click="store.validate(body())"
            >
              校验输入</button
            ><button
              class="primary"
              :disabled="store.busy || (!store.checked && !store.pending)"
              type="submit"
            >
              {{
                store.pending
                  ? "重试原提交"
                  : store.busy
                    ? "正在处理…"
                    : "创建并运行"
              }}
            </button>
          </div>
          <p v-if="store.checked" role="status">
            校验通过，可创建受控实验。创建时会再次核验资料与预算。
          </p>
        </form>
      </template>
      <template v-else-if="store.selected">
        <header class="evaluation-heading">
          <div>
            <h2>
              {{ label(store.selected.status)
              }}<span
                v-if="
                  store.selected.cancel_requested &&
                  active(store.selected.status)
                "
              >
                · 取消中</span
              >
            </h2>
            <small>{{ store.selected.job_id }}</small>
          </div>
          <button
            v-if="active(store.selected.status)"
            :disabled="store.busy || store.selected.cancel_requested"
            @click="store.cancel"
          >
            请求取消
          </button>
        </header>
        <p class="evaluation-note">
          受控实验：真实本地检索，脚本生成与评分。{{
            active(store.selected.status) ? "页面会定时读取已保存进度。" : ""
          }}
        </p>
        <p
          v-if="store.selected.error_code"
          class="evaluation-alert"
          role="alert"
        >
          执行错误：{{ store.selected.error_code }}
        </p>
        <section aria-label="逐题执行" class="evaluation-section">
          <h3>逐题定位</h3>
          <p>选择案例查看已保存的答卷、引用与执行情况。</p>
          <div class="evaluation-case-list">
            <button
              v-for="item in store.selected.cases"
              :key="item.case_id"
              :aria-pressed="store.caseDetail?.case_id === item.case_id"
              @click="store.chooseCase(item.case_id)"
            >
              {{ item.case_id }} <span>{{ label(item.status) }}</span>
            </button>
          </div>
          <p v-if="store.caseError" role="alert" class="error">
            {{ store.caseError }}
          </p>
          <article v-if="store.caseDetail" class="evaluation-case">
            <h4>{{ store.caseDetail.question }}</h4>
            <p>
              {{ label(store.caseDetail.status) }} · 请求补充：{{
                store.caseDetail.requested ? "是" : "否"
              }}
              · 已接收：{{ store.caseDetail.accepted ? "是" : "否" }} ·
              已续接：{{ store.caseDetail.resumed ? "是" : "否" }}
            </p>
            <p v-if="store.caseDetail.error_code" class="error">
              执行错误：{{ store.caseDetail.error_code }}
            </p>
            <p v-if="store.caseDetail.counts">
              生成/策略模型尝试 {{ store.caseDetail.counts.model_attempts }} ·
              检索 {{ store.caseDetail.counts.retrieval_calls }} · 工具
              {{ store.caseDetail.counts.tool_calls }}
            </p>
            <h5>已保存答卷</h5>
            <pre v-if="store.caseDetail.answer">{{
              store.caseDetail.answer
            }}</pre>
            <p v-else>尚无已保存答卷。</p>
            <ul v-if="store.caseDetail.materials.length">
              <li v-for="m in store.caseDetail.materials" :key="m.material_id">
                [{{ m.material_id }}] {{ m.source_id }} · 字符区间 [{{
                  m.start
                }}, {{ m.end }})
              </li>
            </ul>
            <p v-if="store.caseDetail.judge">
              受控 Judge：正确性 {{ store.caseDetail.judge.correctness }} /
              完整性 {{ store.caseDetail.judge.completeness }} / 依据性
              {{ store.caseDetail.judge.grounding }}（各项 0–2）
            </p>
            <p v-else>
              Judge 未评价<span v-if="store.caseDetail.judge_error"
                >：{{ store.caseDetail.judge_error }}</span
              >
            </p>
          </article>
        </section>
        <template v-if="store.report">
          <section class="evaluation-section" aria-label="比较报告">
            <h3>报告与 baseline</h3>
            <p>
              选择已完成的实验作为参照；指纹不一致时，需要明确允许变化的实验变量。
            </p>
            <label for="baseline">参照实验</label
            ><select id="baseline" v-model="baseline">
              <option value="">请选择 baseline</option>
              <option
                v-for="item in options"
                :key="item.job_id"
                :value="item.job_id"
              >
                {{ item.dataset_id }} · {{ date(item.created_at) }} ·
                {{ item.job_id.slice(0, 8) }}
              </option>
            </select>
            <fieldset class="evaluation-variables">
              <legend>本次允许变化的变量</legend>
              <label v-for="(name, key) in variables" :key="key"
                ><input v-model="changes" type="checkbox" :value="key" />{{
                  name
                }}</label
              >
            </fieldset>
            <button
              :disabled="!baseline || store.comparing"
              @click="store.compare(baseline, changes)"
            >
              {{ store.comparing ? "比较中…" : "比较报告" }}
            </button>
            <p v-if="store.compareError" class="error" role="alert">
              {{ store.compareError }}
            </p>
          </section>
          <section
            v-if="currentReport"
            class="evaluation-section"
            aria-label="评测报告"
          >
            <h3>
              {{ store.comparison ? "比较结果" : "实验报告" }} ·
              {{ label(currentReport.decision) }}
            </h3>
            <p>来源：真实本地检索 / 受控生成 / 受控 Judge</p>
            <p>
              模型总尝试（含 Judge）：{{ currentReport.total_model_attempts }}
            </p>
            <div class="evaluation-table">
              <table>
                <caption>
                  结构指标（不等于答案语义正确性）
                </caption>
                <thead>
                  <tr>
                    <th scope="col">指标</th>
                    <th scope="col">结果</th>
                    <th scope="col">已评价 / 应评价</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="(value, key) in currentReport.structural.report
                      .quality.metrics"
                    :key="key"
                  >
                    <th scope="row">{{ metrics[key] ?? key }}</th>
                    <td>{{ value.value === null ? "未评价" : value.value }}</td>
                    <td>{{ value.evaluated }} / {{ value.eligible }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="evaluation-table">
              <table>
                <caption>
                  受控 Judge（各项 0–2；未评价不补零）
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Case</th>
                    <th scope="col">正确性</th>
                    <th scope="col">完整性</th>
                    <th scope="col">依据性</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="item in currentReport.evaluations"
                    :key="item.case_id"
                  >
                    <th scope="row">{{ item.case_id }}</th>
                    <td>{{ item.judge?.correctness ?? "未评价" }}</td>
                    <td>{{ item.judge?.completeness ?? "未评价" }}</td>
                    <td>{{ item.judge?.grounding ?? "未评价" }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-for="d in currentReport.judge_deltas" :key="d.metric">
              {{ d.metric }}：{{ d.baseline ?? "未评价" }} →
              {{ d.candidate ?? "未评价" }} · 差值 {{ d.change ?? "不可用" }}
            </p>
            <p
              v-for="d in currentReport.structural.gate.differences"
              :key="d.field"
            >
              实验差异：{{ variables[d.field] }} ·
              {{ d.permitted ? "允许变化" : "未允许变化" }}
            </p>
            <details>
              <summary>Gate 规则与诊断</summary>
              <ul>
                <li
                  v-for="rule in currentReport.structural.gate.rules"
                  :key="rule.name"
                >
                  [{{ rule.status }}] {{ rule.name }}：期望
                  {{ rule.expected }}，实际 {{ rule.actual }}
                </li>
              </ul>
            </details>
          </section>
        </template>
      </template>
      <p v-else-if="jobId && !store.error" role="status">正在读取实验…</p>
    </div>
  </section>
</template>
<style scoped>
.evaluation-workspace {
  display: grid;
  grid-template-columns: 270px minmax(0, 1fr);
  gap: 32px;
  min-height: 600px;
  width: 100%;
}
.evaluation-directory {
  border-right: 1px solid #dde2dc;
  padding-right: 24px;
  min-width: 0;
}
.evaluation-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
h1 {
  font-size: 22px;
  letter-spacing: 0;
}
h2 {
  font-size: 28px;
  letter-spacing: 0;
  margin: 12px 0;
}
h3 {
  font-size: 20px;
  margin: 0 0 16px;
}
h4 {
  font-size: 17px;
}
h5 {
  font-size: 15px;
  margin-bottom: 12px;
}
.evaluation-content {
  min-width: 0;
  max-width: 1000px;
}
p {
  line-height: 1.65;
  max-width: 75ch;
}
.evaluation-note,
small {
  font-size: 13px;
  color: #536259;
  overflow-wrap: anywhere;
}
.evaluation-history {
  display: flex;
  flex-direction: column;
  margin: 24px 0;
  max-height: 65vh;
  overflow: auto;
}
.evaluation-history a {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px 12px;
  border-bottom: 1px solid #dde2dc;
  overflow-wrap: anywhere;
}
.evaluation-history a:hover {
  background: #edf2ed;
}
.evaluation-history a[aria-current="page"] {
  background: #e4ece4;
}
form {
  margin-top: 28px;
}
fieldset {
  border: 0;
  border-top: 1px solid #dde2dc;
  padding: 24px 0;
  margin: 16px 0;
  min-width: 0;
}
legend {
  padding: 0 12px 0 0;
  font-weight: 600;
}
label {
  display: block;
  font-size: 14px;
  margin: 12px 0 8px;
}
select,
input {
  max-width: 100%;
  font: inherit;
}
select,
input[type="file"],
input[type="number"] {
  width: 100%;
  padding: 12px;
  background: #fff;
  border: 1px solid #bac7bc;
  border-radius: 6px;
  color: inherit;
}
input[type="file"] {
  font-size: 14px;
}
.evaluation-budgets {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.evaluation-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.evaluation-alert {
  border: 1px solid #9a4c35;
  background: #fbf0e9;
  padding: 12px 16px;
  overflow-wrap: anywhere;
}
.evaluation-section {
  border-top: 1px solid #dde2dc;
  padding-top: 28px;
  margin-top: 32px;
}
.evaluation-case-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.evaluation-case-list button[aria-pressed="true"] {
  outline: 2px solid #38624d;
  outline-offset: 2px;
}
.evaluation-case-list span {
  font-size: 12px;
}
.evaluation-case {
  margin-top: 24px;
  padding: 4px 0;
}
.evaluation-case pre {
  font: inherit;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.7;
  background: #eef2ec;
  padding: 20px;
}
.evaluation-variables {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin: 16px 0;
  padding: 12px 0;
}
.evaluation-variables label {
  display: flex;
  align-items: center;
  gap: 8px;
}
.evaluation-variables input[type="checkbox"] {
  width: 18px;
  height: 18px;
  flex: 0 0 18px;
  padding: 0;
  margin: 0;
}
.evaluation-variables label {
  white-space: nowrap;
}
.evaluation-table {
  overflow-x: auto;
  margin: 24px 0;
}
table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
  font-size: 14px;
}
caption {
  text-align: left;
  font-weight: 600;
  margin-bottom: 12px;
}
th,
td {
  border-bottom: 1px solid #dde2dc;
  padding: 12px 8px;
  white-space: nowrap;
}
details {
  margin: 20px 0;
  overflow-wrap: anywhere;
}
summary {
  cursor: pointer;
  font-weight: 600;
}
li {
  line-height: 1.7;
  overflow-wrap: anywhere;
}
@media (max-width: 800px) {
  .evaluation-workspace {
    grid-template-columns: 1fr;
    gap: 24px;
  }
  .evaluation-directory {
    border-right: 0;
    border-bottom: 1px solid #dde2dc;
    padding: 0 0 24px;
  }
  .evaluation-history {
    max-height: 190px;
    margin: 16px 0;
  }
  .evaluation-budgets {
    grid-template-columns: 1fr;
  }
  h2 {
    font-size: 25px;
  }
  .evaluation-table {
    max-width: 100%;
  }
}
</style>

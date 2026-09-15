<script setup lang="ts">
// 结构化报告区块渲染器（[前端详细设计 §11.4] blocks 引擎）：
// 按 block.type 分发 conclusion / evidence / dispute / limitation 四种形态；
// 根元素带 block.id 供目录锚点定位；数据与动作全部由 props/emit 注入，组件自身不取数。
import { computed, ref } from 'vue'
import UiBadge from '@/components/ui/feedback/UiBadge.vue'
import CitationMarker from '@/components/business/CitationMarker.vue'
import ConflictBlock from '@/components/business/ConflictBlock.vue'
import { claimConfidenceLabel, claimConfidenceVariant, reportBlockLabel } from '@/services/i18n/zh-CN'
import type {
  ConflictEvidenceSummary,
  ConflictResponse,
  ReportBlock,
  ReportCitationItem
} from '@/services/api/types'

const props = withDefaults(
  defineProps<{
    block: ReportBlock
    index: number
    // 筛选灰化态（非 dispute 块在「只看分歧」激活时置灰）
    dimmed?: boolean
    citationByMarker: Map<string, ReportCitationItem>
    // dispute 块关联的冲突列表项；缺失时按 danger 文本块渲染（数据缺字段的正常分支）
    conflict?: ConflictResponse | null
    evidenceA?: ConflictEvidenceSummary | null
    evidenceB?: ConflictEvidenceSummary | null
  }>(),
  {
    dimmed: false,
    conflict: null,
    evidenceA: null,
    evidenceB: null
  }
)

const emit = defineEmits<{
  (e: 'open-source', evidenceId: string): void
}>()

// 目录锚点：fixture 与 M2-7 契约均带 id；缺省时按序号生成保证可定位
const anchorId = computed(() => props.block.id ?? `report-block-${props.index}`)

const snippetOpen = ref(false)

function toggleSnippet(): void {
  snippetOpen.value = !snippetOpen.value
}

function onMarkerOpen(evidenceId: string): void {
  emit('open-source', evidenceId)
}
</script>

<template>
  <article
    :id="anchorId"
    class="rb"
    :class="[`rb--${block.type}`, { 'is-dimmed': dimmed }]"
  >
    <!-- 论断结论：正文段落 + 结尾行内角标组 + 置信度徽标（推断不以事实样式呈现） -->
    <template v-if="block.type === 'conclusion'">
      <p class="rb__paragraph">
        <span class="rb__text">{{ block.text }}</span>
        <span
          v-if="block.citations?.length"
          class="rb__markers"
        >
          <CitationMarker
            v-for="cite in block.citations"
            :key="cite.evidence_id"
            :marker="cite.marker"
            :citation="citationByMarker.get(cite.marker) ?? null"
            :dimmed="dimmed"
            @open="onMarkerOpen"
          />
        </span>
      </p>
      <UiBadge
        v-if="block.confidence"
        class="rb__confidence"
        :variant="claimConfidenceVariant(block.confidence)"
      >
        {{ claimConfidenceLabel(block.confidence) }}
      </UiBadge>
    </template>

    <!-- 证据引用：叙述文本 + 角标，可折叠展开各引用的原文片段 -->
    <template v-else-if="block.type === 'evidence'">
      <p class="rb__eyebrow">
        {{ reportBlockLabel('evidence') }}
      </p>
      <p class="rb__paragraph">
        <span class="rb__text">{{ block.text }}</span>
        <span
          v-if="block.citations?.length"
          class="rb__markers"
        >
          <CitationMarker
            v-for="cite in block.citations"
            :key="cite.evidence_id"
            :marker="cite.marker"
            :citation="citationByMarker.get(cite.marker) ?? null"
            :dimmed="dimmed"
            @open="onMarkerOpen"
          />
        </span>
      </p>
      <button
        v-if="block.citations?.length"
        type="button"
        class="rb__snippet-toggle"
        :aria-expanded="snippetOpen"
        @click="toggleSnippet"
      >
        原文片段（{{ block.citations.length }}）
        <svg
          class="rb__chevron"
          :class="{ 'is-open': snippetOpen }"
          viewBox="0 0 16 16"
          width="12"
          height="12"
          aria-hidden="true"
        >
          <path
            d="M4 6 L8 10 L12 6"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>
      <div
        v-if="snippetOpen"
        class="rb__snippets"
      >
        <p
          v-for="cite in block.citations"
          :key="cite.evidence_id"
          class="rb__snippet"
        >
          <span class="rb__snippet-marker">{{ cite.marker }}</span>
          {{ cite.snippet }}
        </p>
      </div>
    </template>

    <!-- 数据分歧：叙述文本 + 只读 ConflictBlock（展开即见引用索引中的双方证据，无任何裁决入口） -->
    <template v-else-if="block.type === 'dispute'">
      <div
        class="rb__dispute-text"
        :class="{ 'rb__dispute-text--plain': !conflict }"
      >
        <p class="rb__paragraph">
          <span class="rb__text">{{ block.text }}</span>
          <span
            v-if="block.citations?.length"
            class="rb__markers"
          >
            <CitationMarker
              v-for="cite in block.citations"
              :key="cite.evidence_id"
              :marker="cite.marker"
              :citation="citationByMarker.get(cite.marker) ?? null"
              @open="onMarkerOpen"
            />
          </span>
        </p>
      </div>
      <ConflictBlock
        v-if="conflict"
        :conflict="conflict"
        :evidence-a="evidenceA"
        :evidence-b="evidenceB"
      />
    </template>

    <!-- 研究局限：纯展示条目（底部 LimitationSummary 另做汇总） -->
    <template v-else>
      <p class="rb__paragraph rb__paragraph--limitation">
        <span
          class="rb__limitation-dot"
          aria-hidden="true"
        />
        <span class="rb__text">{{ block.text }}</span>
        <span
          v-if="block.citations?.length"
          class="rb__markers"
        >
          <CitationMarker
            v-for="cite in block.citations"
            :key="cite.evidence_id"
            :marker="cite.marker"
            :citation="citationByMarker.get(cite.marker) ?? null"
            :dimmed="dimmed"
            @open="onMarkerOpen"
          />
        </span>
      </p>
    </template>
  </article>
</template>

<style scoped>
.rb {
  padding: var(--space-4) 0;
  border-bottom: 1px solid var(--color-border);
  scroll-margin-top: var(--space-8);
}

.rb.is-dimmed {
  opacity: 0.4;
}

.rb__paragraph {
  margin: 0;
  font-size: var(--font-sm);
  line-height: 1.8;
  color: var(--color-text);
}

.rb__markers {
  white-space: nowrap;
}

.rb__confidence {
  margin-top: var(--space-2);
}

/* evidence：弱化眉题 + 原文片段折叠 */
.rb__eyebrow {
  margin: 0 0 var(--space-1);
  font-size: var(--font-xs);
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--color-text-muted);
}

.rb__snippet-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  margin-top: var(--space-2);
  padding: 0;
  border: none;
  background: transparent;
  color: var(--brand-700);
  font-size: var(--font-xs);
  cursor: pointer;
}

.rb__chevron {
  transition: transform var(--motion-fast) var(--ease-out);
}

.rb__chevron.is-open {
  transform: rotate(180deg);
}

.rb__snippets {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

.rb__snippet {
  margin: 0;
  padding: var(--space-2) var(--space-3);
  border-left: 3px solid var(--brand-500);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  background: var(--neutral-50);
  font-size: var(--font-xs);
  line-height: 1.7;
  color: var(--neutral-600);
}

.rb__snippet-marker {
  margin-right: var(--space-1);
  color: var(--brand-700);
  font-weight: 600;
}

/* dispute：叙述文本与 ConflictBlock 间距；无冲突数据时文本块自身使用 danger 描边 */
.rb__dispute-text {
  margin-bottom: var(--space-3);
}

.rb__dispute-text--plain .rb__paragraph {
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--danger-500);
  border-radius: var(--radius-md);
  background: var(--danger-50);
}

/* limitation：条目形态 */
.rb__paragraph--limitation {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  color: var(--neutral-600);
}

.rb__limitation-dot {
  flex: 0 0 auto;
  width: 6px;
  height: 6px;
  margin-top: 9px;
  border-radius: var(--radius-full);
  background: var(--neutral-400);
}
</style>

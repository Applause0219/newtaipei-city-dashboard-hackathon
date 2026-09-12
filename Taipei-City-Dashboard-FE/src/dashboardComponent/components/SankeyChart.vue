<!-- Developed by Taipei Urban Intelligence Center 2026 -->
<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

const props = defineProps([
	"chart_config",
	"activeChart",
	"series",
	"map_config",
	"map_filter",
	"map_filter_on",
]);

const wrapRef = ref(null);
const chartWidth = ref(860);
const chartPixelHeight = ref(220);
let resizeObserver = null;

function hexToRgb(hex) {
	const s = String(hex || "").replace("#", "");
	if (s.length !== 6) return null;
	const n = Number.parseInt(s, 16);
	if (!Number.isFinite(n)) return null;
	return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

function lighten(hex, ratio = 0.25) {
	const rgb = hexToRgb(hex);
	if (!rgb) return hex;
	const r = Math.round(rgb.r + (255 - rgb.r) * ratio);
	const g = Math.round(rgb.g + (255 - rgb.g) * ratio);
	const b = Math.round(rgb.b + (255 - rgb.b) * ratio);
	return `rgb(${r}, ${g}, ${b})`;
}

function normalizeRows(raw) {
	if (Array.isArray(raw)) return raw;
	if (!raw || typeof raw !== "object") return [];

	// Common API wrappers
	const wrapped =
		raw.data || raw.rows || raw.items || raw.series || raw.result || null;
	if (Array.isArray(wrapped)) return wrapped;

	// Columnar payload fallback: { x_axis:[], y_axis:[], data:[] }
	if (
		Array.isArray(raw.x_axis) &&
		Array.isArray(raw.y_axis) &&
		Array.isArray(raw.data)
	) {
		const n = Math.min(raw.x_axis.length, raw.y_axis.length, raw.data.length);
		return Array.from({ length: n }, (_, i) => ({
			x_axis: raw.x_axis[i],
			y_axis: raw.y_axis[i],
			data: raw.data[i],
			color: Array.isArray(raw.color) ? raw.color[i] : null,
		}));
	}

	return [];
}

const sankeyLinks = computed(() => {
	const raw = props.series;
	const fromArr = Array.isArray(raw?.edges) ? raw.edges : normalizeRows(raw);
	return fromArr
		.map((it) => ({
			source: String(it.source ?? it.from ?? it.x_axis ?? it.x ?? "").trim(),
			target: String(
				it.target ??
					it.to ??
					it.y_axis ??
					(typeof it.y === "string" ? it.y : "") ??
					"",
			).trim(),
			value: Number(it.value ?? it.data ?? it.y ?? 0),
			color: it.color ? String(it.color) : null,
		}))
		.filter((l) => l.source && l.target && Number.isFinite(l.value) && l.value > 0);
});

const palette = computed(() => {
	const c = props.chart_config?.color;
	return Array.isArray(c) && c.length > 0 ? c : ["#4EA3FF", "#F5A623"];
});

function computeLevels(links) {
	const nodes = new Set();
	const indeg = new Map();
	const out = new Map();
	for (const l of links) {
		nodes.add(l.source);
		nodes.add(l.target);
		indeg.set(l.target, (indeg.get(l.target) || 0) + 1);
		indeg.set(l.source, indeg.get(l.source) || 0);
		if (!out.has(l.source)) out.set(l.source, []);
		out.get(l.source).push(l.target);
	}
	const q = [];
	for (const n of nodes) if ((indeg.get(n) || 0) === 0) q.push(n);
	const level = new Map();
	for (const n of nodes) level.set(n, 0);
	while (q.length) {
		const n = q.shift();
		const nexts = out.get(n) || [];
		for (const m of nexts) {
			level.set(m, Math.max(level.get(m) || 0, (level.get(n) || 0) + 1));
			indeg.set(m, (indeg.get(m) || 0) - 1);
			if ((indeg.get(m) || 0) === 0) q.push(m);
		}
	}
	let maxL = 0;
	for (const n of nodes) maxL = Math.max(maxL, level.get(n) || 0);
	return { level, maxL, nodes: [...nodes] };
}

const sankeyLayout = computed(() => {
	const links = sankeyLinks.value;
	if (!links.length) {
		return {
			links: [],
			nodes: [],
			width: chartWidth.value,
			height: Math.max(170, chartPixelHeight.value - 4),
		};
	}

	const W = chartWidth.value;
	const H = Math.max(170, chartPixelHeight.value - 4);
	const pad = { left: 14, right: 20, top: 10, bottom: 10 };
	const nodeW = Number(props.chart_config?.node_width) || 20;
	const nodeGap = Number(props.chart_config?.node_gap) || 12;

	const { level, maxL, nodes } = computeLevels(links);
	const layerCfg = Number(props.chart_config?.sankey_layers);
	const layerCount = Number.isFinite(layerCfg) && layerCfg > 1 ? Math.floor(layerCfg) : maxL + 1;
	const toLayer = (l) =>
		maxL <= 0
			? 0
			: Math.round((l / maxL) * Math.max(0, layerCount - 1));

	const layerMap = new Map();
	const nodeStat = new Map();
	const incomingByNode = new Map();
	const parentOrder = new Map();
	for (const n of nodes) nodeStat.set(n, { in: 0, out: 0 });
	for (const l of links) {
		nodeStat.get(l.source).out += l.value;
		nodeStat.get(l.target).in += l.value;
		if (!incomingByNode.has(l.target)) incomingByNode.set(l.target, []);
		incomingByNode.get(l.target).push(l);
	}
	for (const n of nodes) {
		const layer = toLayer(level.get(n) || 0);
		if (!layerMap.has(layer)) layerMap.set(layer, []);
		layerMap.get(layer).push(n);
	}

	const roots = nodes.filter((n) => (incomingByNode.get(n) || []).length === 0);
	const firstLayer = (layerMap.get(1) || []).slice();
	const branchColorByFirstNode = new Map();
	const baseBlue = "#4EA3FF";
	const baseOrange = "#F5A623";
	if (firstLayer.length > 0) {
		const blueNode =
			firstLayer.find((n) => String(n).includes("通勤")) || firstLayer[0];
		const orangeNode =
			firstLayer.find((n) => String(n).includes("其他") && n !== blueNode) ||
			firstLayer.find((n) => n !== blueNode) ||
			firstLayer[0];
		branchColorByFirstNode.set(blueNode, baseBlue);
		branchColorByFirstNode.set(orangeNode, baseOrange);
	}
	for (let i = 2; i < firstLayer.length; i++) {
		if (!branchColorByFirstNode.has(firstLayer[i])) {
			branchColorByFirstNode.set(firstLayer[i], palette.value[i % palette.value.length]);
		}
	}
	firstLayer.forEach((n, i) => parentOrder.set(n, i));

	const branchByNode = new Map();
	for (const r of roots) branchByNode.set(r, "__root__");
	for (const n of firstLayer) branchByNode.set(n, n);
	const sortedLayers = [...layerMap.keys()].sort((a, b) => a - b);
	for (const layer of sortedLayers) {
		const arr = layerMap.get(layer) || [];
		for (const n of arr) {
			if (branchByNode.has(n)) continue;
			const ins = incomingByNode.get(n) || [];
			if (!ins.length) continue;
			ins.sort((a, b) => b.value - a.value);
			const parent = ins[0].source;
			branchByNode.set(n, branchByNode.get(parent) || parent);
		}
	}

	for (const [layer, arr] of layerMap.entries()) {
		arr.sort((a, b) => {
			if (layer <= 1) return 0;
			const aIn = incomingByNode.get(a) || [];
			const bIn = incomingByNode.get(b) || [];
			const aParent = aIn.length ? aIn.sort((x, y) => y.value - x.value)[0].source : "";
			const bParent = bIn.length ? bIn.sort((x, y) => y.value - x.value)[0].source : "";
			const aPo = parentOrder.has(aParent) ? parentOrder.get(aParent) : 999;
			const bPo = parentOrder.has(bParent) ? parentOrder.get(bParent) : 999;
			if (aPo !== bPo) return aPo - bPo;
			if (aParent !== bParent) return String(aParent).localeCompare(String(bParent));
			return (nodeStat.get(b).in || 0) - (nodeStat.get(a).in || 0);
		});
	}

	const maxNodeValue = Math.max(
		1,
		...nodes.map((n) => Math.max(nodeStat.get(n).in, nodeStat.get(n).out)),
	);
	const maxNodesInLayer = Math.max(1, ...[...layerMap.values()].map((arr) => arr.length));
	const usableH = H - pad.top - pad.bottom - nodeGap * (maxNodesInLayer - 1);
	const pxPerVal = Math.max(2 / maxNodeValue, usableH / maxNodeValue);

	const xStep = layerCount <= 1 ? 0 : (W - pad.left - pad.right - nodeW) / (layerCount - 1);
	const nodePos = new Map();
	for (const [layer, arr] of layerMap.entries()) {
		const heights = arr.map((n) =>
			Math.max(7, Math.max(nodeStat.get(n).in, nodeStat.get(n).out) * pxPerVal),
		);
		const totalH = heights.reduce((a, b) => a + b, 0) + nodeGap * Math.max(0, arr.length - 1);
		let y = pad.top + (H - pad.top - pad.bottom - totalH) / 2;
		for (let i = 0; i < arr.length; i++) {
			const n = arr[i];
			nodePos.set(n, {
				id: n,
				layer,
				x: pad.left + layer * xStep,
				y,
				w: nodeW,
				h: heights[i],
				inOffset: 0,
				outOffset: 0,
			});
			y += heights[i] + nodeGap;
		}
	}

	const linksSorted = links
		.map((l, i) => ({ ...l, _i: i }))
		.sort((a, b) => {
			const sa = nodePos.get(a.source);
			const sb = nodePos.get(b.source);
			const ta = nodePos.get(a.target);
			const tb = nodePos.get(b.target);
			if (!sa || !sb || !ta || !tb) return 0;
			if (sa.y !== sb.y) return sa.y - sb.y;
			return ta.y - tb.y;
		});

	const linkPaths = linksSorted.map((l, i) => {
		const s = nodePos.get(l.source);
		const t = nodePos.get(l.target);
		if (!s || !t) return null;
		const th = Math.max(3, l.value * pxPerVal);
		const sy = s.y + s.outOffset + th / 2;
		const ty = t.y + t.inOffset + th / 2;
		s.outOffset += th;
		t.inOffset += th;
		const x1 = s.x + s.w;
		const x2 = t.x;
		const isNearlyStraight = Math.abs(sy - ty) < 8;
		const c = Math.max(30, (x2 - x1) * 0.45);
		const branch = branchByNode.get(l.source) === "__root__" ? l.target : branchByNode.get(l.source);
		const baseColor = branchColorByFirstNode.get(branch) || palette.value[i % palette.value.length];
		const depth = Math.max(0, level.get(l.source) || 0);
		const strokeSolid = depth <= 0 ? baseColor : lighten(baseColor, 0.28);
		const targetBranch = branchByNode.get(l.target);
		const targetBase =
			branchColorByFirstNode.get(targetBranch) ||
			branchColorByFirstNode.get(l.target) ||
			strokeSolid;
		const strokeTo = Math.max(0, level.get(l.target) || 0) <= 1
			? targetBase
			: lighten(targetBase, 0.28);
		const gradientId = `sankey-grad-${i}-${String(l.source).replace(/[^\w-]/g, "_")}-${String(l.target).replace(/[^\w-]/g, "_")}`;
		return {
			id: `${l.source}->${l.target}-${i}`,
			d: isNearlyStraight
				? `M ${x1} ${sy} L ${x2} ${ty}`
				: `M ${x1} ${sy} C ${x1 + c} ${sy}, ${x2 - c} ${ty}, ${x2} ${ty}`,
			gradientId,
			strokeSolid,
			strokeTo,
			width: th,
			value: l.value,
			labelX: (x1 + x2) / 2,
			labelY: (sy + ty) / 2 - 2,
			label: th >= 8 ? `${l.value}%` : "",
		};
	}).filter(Boolean);

	const nodesOut = [...nodePos.values()].map((n, i) => ({
		...n,
		fill: (() => {
			const nodeBranch = branchByNode.get(n.id);
			const baseColor = branchColorByFirstNode.get(nodeBranch) || palette.value[i % palette.value.length];
			const d = Math.max(0, level.get(n.id) || 0);
			if (nodeBranch === "__root__") return lighten(baseBlue, 0.55);
			return d <= 1 ? baseColor : lighten(baseColor, 0.3);
		})(),
		label: n.id,
		labelX:
			n.layer === 0
				? n.x + n.w + 6
				: n.layer >= layerCount - 1
					? n.x - 6
					: n.x + n.w + 6,
		labelY: n.y + Math.max(12, Math.min(n.h - 2, 14)),
		labelAnchor:
			n.layer === 0
				? "start"
				: n.layer >= layerCount - 1
					? "end"
					: "start",
		// Keep text clean: thin downstream nodes rely on hover only.
		showLabel: n.layer <= 1 ? n.h >= 9 : n.h >= 16,
	}));
	return { links: linkPaths, nodes: nodesOut, width: W, height: H, maxLayer: layerCount - 1 };
});

function updateSize() {
	const host = wrapRef.value;
	const w = host?.clientWidth || 860;
	const h = host?.clientHeight || Number(props.chart_config?.height) || 220;
	chartWidth.value = Math.max(320, w - 2);
	chartPixelHeight.value = Math.max(170, h - 2);
}

onMounted(() => {
	updateSize();
	resizeObserver = new ResizeObserver(updateSize);
	if (wrapRef.value) resizeObserver.observe(wrapRef.value);
});

onBeforeUnmount(() => {
	if (resizeObserver) resizeObserver.disconnect();
});
</script>

<template>
	<div v-if="activeChart === 'SankeyChart'" ref="wrapRef" class="sankeychart">
		<svg :viewBox="`0 0 ${sankeyLayout.width} ${sankeyLayout.height}`" width="100%" height="100%">
			<defs>
				<linearGradient
					v-for="link in sankeyLayout.links"
					:key="`${link.id}-grad`"
					:id="link.gradientId"
					x1="0%"
					y1="0%"
					x2="100%"
					y2="0%"
				>
					<stop offset="0%" :stop-color="link.strokeSolid" />
					<stop offset="100%" :stop-color="link.strokeTo" />
				</linearGradient>
			</defs>
			<g>
				<path
					v-for="link in sankeyLayout.links"
					:key="link.id"
					:d="link.d"
					fill="none"
					:stroke="`url(#${link.gradientId})`"
					:stroke-opacity="0.42"
					:stroke-width="link.width"
					stroke-linecap="butt"
				>
					<title>{{ `${link.id.split("->")[1]?.split("-")[0] || ""}：${link.value}%` }}</title>
				</path>
			</g>
			<g>
				<g v-for="node in sankeyLayout.nodes" :key="node.id">
					<rect :x="node.x" :y="node.y" :width="node.w" :height="node.h" rx="0" :fill="node.fill" fill-opacity="0.95" />
					<text
						v-if="node.showLabel"
						:x="node.labelX"
						:y="node.labelY"
						:text-anchor="node.labelAnchor"
						class="sankeychart__label"
					>
						{{ node.label }}
					</text>
				</g>
			</g>
		</svg>
		<div v-if="sankeyLayout.links.length < 2" class="sankeychart__hint">
			資料不足或格式不符（需至少 2 條有效流向）
		</div>
	</div>
</template>

<style scoped lang="scss">
.sankeychart {
	width: 100%;
	height: 100%;
	position: relative;
}

.sankeychart__label {
	font-size: 12px;
	font-weight: 700;
	fill: #f3f6fb;
	paint-order: stroke;
	stroke: rgba(23, 28, 36, 0.8);
	stroke-width: 1.4px;
	stroke-linejoin: round;
	pointer-events: none;
	letter-spacing: 0.2px;
}

.sankeychart__hint {
	position: absolute;
	left: 0;
	right: 0;
	bottom: 6px;
	text-align: center;
	font-size: 12px;
	color: var(--color-complementary-text);
	opacity: 0.8;
}

</style>

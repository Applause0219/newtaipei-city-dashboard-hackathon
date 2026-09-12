<!-- Developed by Taipei Urban Intelligence Center 2023-2024-->

<script setup>
import { computed, ref, watch, nextTick } from "vue";
import VueApexCharts from "vue3-apexcharts";

const props = defineProps([
	"chart_config",
	"activeChart",
	"series",
	"map_config",
	"map_filter",
	"map_filter_on",
]);

const emits = defineEmits([
	"filterByParam",
	"filterByLayer",
	"clearByParamFilter",
	"clearByLayerFilter",
	"fly",
]);

const cellSize = 28;
const heatmapScrollRef = ref(null);

const heatmapData = computed(() => {
	let output = {};
	let highest = 0;
	let sum = 0;

	if (props.series.length === 1) {
		props.series[0].data.forEach((item) => {
			output[item.x] = item.y;
			if (item.y > highest) highest = item.y;
			sum += item.y;
		});
	} else {
		props.series.forEach((serie) => {
			for (let i = 0; i < props.chart_config.categories.length; i++) {
				if (!output[props.chart_config.categories[i]]) {
					output[props.chart_config.categories[i]] = 0;
				}

				output[props.chart_config.categories[i]] += +serie.data[i];

				if (+serie.data[i] > highest) highest = +serie.data[i];
			}
		});

		sum = Object.values(output).reduce((a, b) => a + b, 0);
	}

	output.highest = highest;
	output.sum = sum;

	return output;
});

const chartHeight = computed(() => {
	const yCount = props.series?.length || 1;
	return Math.max(220, yCount * cellSize + 92);
});

const chartWidth = computed(() => {
	const xCount = props.chart_config.categories?.length || 1;
	const yAxisWidth = 90;
	return Math.max(600, xCount * cellSize + yAxisWidth);
});

const colorScale = computed(() => {
	const ranges = props.chart_config.color.map((el, index) => ({
		to: Math.floor(
			(heatmapData.value.highest / props.chart_config.color.length) *
				(props.chart_config.color.length - index),
		),
		from:
			Math.floor(
				(heatmapData.value.highest / props.chart_config.color.length) *
					(props.chart_config.color.length - index - 1),
			) + 1,
		color: el,
	}));

	ranges.unshift({
		to: 0,
		from: 0,
		color: "#444444",
	});

	return ranges;
});

const chartOptions = computed(() => ({
	chart: {
		stacked: true,
		toolbar: {
			show: false,
		},
	},
	dataLabels: {
		distributed: true,
		style: {
			fontSize: "12px",
			fontWeight: "normal",
		},
	},
	grid: {
		show: false,
		padding: {
			left: 10,
			right: 24,
			top: 0,
			bottom: 0,
		},
	},
	legend: {
		show: false,
	},
	markers: {
		size: 3,
		strokeWidth: 0,
	},
	plotOptions: {
		heatmap: {
			enableShades: false,
			radius: 5,
			colorScale: {
				ranges: colorScale.value,
			},
		},
	},
	stroke: {
		show: true,
		width: 4,
		colors: ["#282a2c"],
	},
	tooltip: {
		custom: function ({ series, seriesIndex, dataPointIndex, w }) {
			return (
				'<div class="chart-tooltip">' +
				"<h6>" +
				`${w.globals.labels[dataPointIndex]}-${w.globals.seriesNames[seriesIndex]}` +
				"</h6>" +
				"<span>" +
				`${series[seriesIndex][dataPointIndex]}` +
				`${props.chart_config.unit}` +
				"</span>" +
				"</div>"
			);
		},
	},
	xaxis: {
		axisBorder: {
			show: false,
		},
		axisTicks: {
			show: false,
		},
		categories: props.chart_config.categories || [],
		labels: {
			offsetX: 0,
			offsetY: 5,
			rotate: -45,
			rotateAlways: true,
			formatter: function (value) {
				return value.length > 7 ? value.slice(0, 6) + "..." : value;
			},
		},
		tooltip: {
			enabled: false,
		},
		type: "category",
	},
	yaxis: {
		labels: {
			show: true,
			minWidth: 70,
			maxWidth: 90,
			offsetY: 2, // ⭐ 往下微調，讓文字對齊格子中心
			style: {
				fontSize: "12px",
				colors: "var(--color-complement-text)",
			},
		},
	},
}));

watch(
	() => [props.series, props.chart_config.categories],
	async () => {
		await nextTick();
		if (heatmapScrollRef.value) {
			heatmapScrollRef.value.scrollLeft = 0;
		}
	},
	{ deep: true, immediate: true },
);

const selectedIndex = ref(null);

function handleDataSelection(_e, _chartContext, config) {
	if (!props.map_filter || !props.map_filter_on) return;

	const key = `${config.dataPointIndex}-${config.seriesIndex}`;

	if (key !== selectedIndex.value) {
		if (props.map_filter.mode === "byParam") {
			emits(
				"filterByParam",
				props.map_filter,
				props.map_config,
				config.w.globals.labels[config.dataPointIndex],
				config.w.globals.seriesNames[config.seriesIndex],
			);
		} else if (props.map_filter.mode === "byLayer") {
			emits(
				"filterByLayer",
				props.map_config,
				config.w.globals.labels[config.dataPointIndex],
			);
		}

		selectedIndex.value = key;
	} else {
		if (props.map_filter.mode === "byParam") {
			emits("clearByParamFilter", props.map_config);
		} else if (props.map_filter.mode === "byLayer") {
			emits("clearByLayerFilter", props.map_config);
		}

		selectedIndex.value = null;
	}
}
</script>

<template>
	<div v-if="activeChart === 'HeatmapChart'" class="heatmapchart">
		<div class="heatmapchart-title">
			<h5>總合</h5>
			<h6>{{ heatmapData.sum }} {{ chart_config.unit }}</h6>
		</div>

		<div ref="heatmapScrollRef" class="heatmap-scroll">
			<VueApexCharts
				:width="chartWidth"
				:height="chartHeight"
				type="heatmap"
				:options="chartOptions"
				:series="series"
				@data-point-selection="handleDataSelection"
			/>
		</div>
	</div>
</template>

<style scoped lang="scss">
.heatmapchart {
	&-title {
		display: flex;
		justify-content: center;
		flex-direction: column;
		margin: -0.2rem 0 -1.5rem;

		h5 {
			margin: 0;
			color: var(--color-complement-text);
		}

		h6 {
			margin: 0;
			color: var(--color-complement-text);
			font-size: var(--font-m);
			font-weight: 400;
		}
	}
}

.heatmap-scroll {
	width: 100%;
	overflow-x: auto;
	overflow-y: hidden;
	padding-bottom: 12px;
	box-sizing: border-box;

	/* ⭐ 加這兩行 */
	display: flex;
	justify-content: flex-start;
}

/* 讓 ApexCharts 原生 y 軸在水平捲動時固定 */
:deep(.apexcharts-yaxis) {
	position: sticky;
	left: 0;
	z-index: 5;
}

/* 避免 y 軸文字被圖表蓋住 */
:deep(.apexcharts-yaxis text) {
	fill: var(--color-complement-text);
}

/* 讓 y 軸背景接近卡片底色，避免滑動時格子從文字後面透出 */
:deep(.apexcharts-yaxis::before) {
	content: "";
	position: absolute;
	inset: 0;
	background: #282a2c;
	z-index: -1;
}
</style>
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import naive from 'naive-ui'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, PieChart, BarChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'

import App from './App.vue'
import router from './router'
import './style.css'

use([
  CanvasRenderer,
  LineChart,
  PieChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
])

const app = createApp(App)
app.component('VChart', VChart)
app.use(createPinia())
app.use(router)
app.use(naive)
app.mount('#app')

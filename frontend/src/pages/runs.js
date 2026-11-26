import { api } from '../api/workspace.js';
import { table } from '../components/common/table.js';
import { heading, empty } from '../components/common/heading.js';
import { status } from '../components/common/status.js';
import { bindForm, notice } from '../hooks/forms.js';
import { navigate } from '../router/routes.js';
import { escapeHtml as e, timeText } from '../utils/format.js';

function shiftOptions(shifts, active) {
  return shifts.map(shift => `<option value="${shift.id}" ${shift.id === active ? 'selected' : ''}>
    ${e(shift.label)} · 余 ${shift.remaining_minutes}/${shift.budget_minutes} 分钟</option>`).join('');
}

export async function runsPage(profile, account, id) {
  const [runs, datasets, shifts] = await Promise.all([api.runs(), api.datasets(), api.shifts()]);
  const parameter = profile.parameter;
  const shiftRule = profile.shift || { label: '本班可用工时 分钟', default: 240, min: 30, max: 1440, step: 5 };
  const activeShift = shifts.find(shift => shift.id === id) || shifts[0];
  const canSubmit = account.role === 'analyst' && datasets.length > 0 && activeShift;
  const openForm = account.role === 'analyst' ? `<details class="intake" ${shifts.length ? '' : 'open'}>
    <summary>开新班次</summary>
    <form id="shift-form">
      <label>班次编号<input name="key" maxlength="60" placeholder="例如 2026-11-26 早班" required></label>
      <label>${e(shiftRule.label)}<input type="number" name="budget_minutes" min="${shiftRule.min}" max="${shiftRule.max}"
        step="${shiftRule.step}" value="${shiftRule.default}" required></label>
      <p class="muted">${e(shiftRule.description || '')}</p>
      <div class="form-error" data-error role="alert"></div>
      <button type="submit">开班</button>
    </form></details>` : '';
  const form = canSubmit ? `<form id="run-form">
    <label>计算单名称<input name="label" maxlength="100" placeholder="例如本班复核" required></label>
    <label>数据版本<select name="dataset_id">${datasets.map(row =>
      `<option value="${row.id}">${e(row.name)} · ${row.count} 行</option>`).join('')}</select></label>
    <label>加入班次<select name="shift_id">${shiftOptions(shifts, activeShift.id)}</select></label>
    <label>${e(parameter.label)}<input type="number" name="parameter" min="${parameter.min}" max="${parameter.max}"
      step="${parameter.step}" value="${parameter.default}" required></label>
    <p class="muted">${e(parameter.description)}</p><div class="form-error" data-error role="alert"></div>
    <button type="submit">提交计算</button></form>` :
    `<p class="muted">${shifts.length ? '分析员可使用已导入的数据版本创建计算单。' : '先开一个班次，再创建计算单。'}</p>`;
  return {
    html: heading(profile.labels.runs, profile.algorithm) +
      `<div class="board"><aside class="side"><h2>计算设置</h2>${shifts.length ?
        `<label>当前班次<select id="choose-shift">${shiftOptions(shifts, activeShift?.id)}</select></label>
        <dl class="meta"><dt>已排耗时</dt><dd>${activeShift.used_minutes} 分钟</dd>
        <dt>剩余工时</dt><dd>${activeShift.remaining_minutes} 分钟</dd></dl>` : ''}${form}${openForm}</aside>
      <section class="work"><div class="work-head"><h2>计算记录</h2><button type="button" class="quiet" id="refresh-runs">刷新</button></div>
      ${runs.length ? table([
        {key:'label',label:'计算单'}, {key:'parameter',label:parameter.label}, {key:'status',label:'状态'},
        {key:'created_at',label:'提交时间'},
      ], runs, (run, column, value) => {
        if (column.key === 'label') return `<a href="#results/${run.id}">${e(value)}</a>`;
        if (column.key === 'status') return status(value);
        return column.key === 'created_at' ? e(timeText(value)) : e(value);
      }) : empty('等待首个计算单', '选择数据、班次和参数后提交计算，随后可以在此查看记录。')}
      <details class="details"><summary>算法与适用边界</summary><p>${e(profile.method)}</p></details></section></div>`,
    mount(root, refresh) {
      root.querySelector('#refresh-runs').onclick = refresh;
      const pick = root.querySelector('#choose-shift');
      if (pick) pick.onchange = () => navigate('runs', pick.value);
      bindForm(root, 'shift-form', async data => {
        const opened = await api.openShift({
          key: data.get('key'), budget_minutes: Number(data.get('budget_minutes')),
        });
        notice('班次已开。');
        navigate('runs', opened.id);
      });
      bindForm(root, 'run-form', async data => {
        const submitted = await api.submit({
          dataset_id: data.get('dataset_id'),
          shift_id: data.get('shift_id'),
          label: data.get('label'),
          parameter: Number(data.get('parameter')),
          request_key: crypto.randomUUID(),
        });
        notice('计算单已提交。');
        navigate('results', submitted.id);
      });
    },
  };
}

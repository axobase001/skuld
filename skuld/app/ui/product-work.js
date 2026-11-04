'use strict';
// Pure presentation of actual product-work receipts; it never schedules a read,
// creates permission, supplies an outcome, or calls a model. Shared with tests.
((root, factory) => {
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.SkuldProductWork=api;
})(globalThis, () => {
  const states=Object.freeze({
    PENDING:['已排队，等待读取','将读取已授权的具体来源，目前还没有读取结果。',true,'pending'],
    RUNNING:['正在读取来源','正在取得来源内容，结果尚未确认。',true,'pending'],
    WAITING_SOURCE:['等待来源读取','调查计划已保留，尚未确认来源读取结果。',true,'pending'],
    RETRY_WAIT:['暂时受阻，等候重试','这次读取尚未完成，将按已限定的次数继续尝试。',true,'pending'],
    WAITING_EXTRACTION:['已取得页面，等待提取','页面已经保存，正在等待提取可核对的信息；尚未形成可用观察。',true,'pending'],
    WAITING_OBSERVATION:['等待记录与核对','来源响应已经取得，正在等待写入观察并核对相关预测。',true,'pending'],
    OBSERVED:['已取得来源记录','实际来源记录已经保留。',false,'observed'],
    CACHED:['已取得缓存记录','拿到的是缓存内容，未当作新的独立观察。',false,'muted'],
    SUPPRESSED:['近期已读过，跳过重复读取','近期已为同一目标取得过该来源，本次没有重复读取。',false,'muted'],
    CANCELLED:['读取已停止','目标或已授权来源发生变化，不再继续这次调查。',false,'muted'],
    BLOCKED:['读取受阻，需要处理','本次还没有可用的调查结果，未知结果不会被当作完成。',false,'blocked'],
    STALE:['情况已变，先重新核对','这份计划依据的目标或情况已经改变，不能把旧计划当作当前进展。',false,'muted'],
  });
  const errors=Object.freeze({ConnectTimeout:'连接超时',ReadTimeout:'读取超时',WriteTimeout:'请求传输超时',PoolTimeout:'连接暂时不可用',ConnectError:'无法连接来源',ReadError:'读取连接中断',RemoteProtocolError:'来源响应异常',HTTPStatusError:'来源返回了暂时无法使用的响应',ValueError:'来源内容或授权条件不符合要求',PermissionError:'来源访问未获允许',RuntimeError:'读取处理尚未完成'});
  function reasonText(reason) {
    if(typeof reason!=='string'||!reason)return '';
    if(reason.includes('within its polling interval'))return '近期已为同一目标读取过这个来源，因此跳过重复读取。';
    if(reason.includes('goal or exact registered source permission changed'))return '目标或已登记来源的读取权限发生变化，已停止这次调查。';
    if(reason.includes('attempt budget was exhausted'))return '读取尝试次数已达到上限，已经停止重试；来源情况仍未知。';
    if(reason.includes('extraction previously failed')||reason.includes('model extraction failed'))return '页面已经取得，但信息提取失败；不会擅自重复调用模型。';
    const type=reason.match(/^([A-Za-z][A-Za-z0-9]*)(?::|$)/)?.[1];
    if(type&&errors[type])return errors[type]+'，这次尚未得到可用证据。';
    return '来源读取没有产出可用证据，需要核对后再继续。';
  }
  function reportsFor(item,graph) {
    const result=item.investigation_result;
    if(!result?.observation_id)return [];
    const record=(graph?.source_events||[]).find(source=>source.source_event_id===result.observation_id&&source.source_type==='ExternalObservation'&&source.content?.source_id===(result.source_id||item.source_id));
    const facts=record?.content?.data?.facts;
    if(!Array.isArray(facts))return [];
    return facts.slice(0,3).filter(fact=>typeof fact?.key==='string'&&Object.prototype.hasOwnProperty.call(fact,'value')).map(fact=>{
      const value=typeof fact.value==='string'?fact.value:JSON.stringify(fact.value);
      return (fact.key+'：'+value).slice(0,220);
    });
  }
  function describe(item,options={}) {
    if(item.kind!=='INVESTIGATE')return {label:item.status==='STALE'?'情况已变，先重新核对':'内部准备 · 可以直接查看',status:item.status,lines:[],reports:[],observedAt:null,ongoing:false,tone:item.status==='STALE'?'muted':'prepared',needsApproval:false,artifactLabel:'阅读完整准备'};
    const status=item.status||item.investigation_result?.status||'UNKNOWN';
    if(status==='PREPARED'&&!item.source_id)return {label:'调查计划已准备',status,lines:['尚未确定已授权的具体来源，这份计划还没有开始读取。'],reports:[],observedAt:null,ongoing:false,tone:'prepared',needsApproval:false,artifactLabel:'阅读完整调查计划'};
    const entry=states[status]||['读取状态待核对','还没有可确认的读取状态，请以实际记录为准。',false,'muted'];
    const result=item.investigation_result||{},reason=reasonText(result.reason);
    const lines=[reason&&['BLOCKED','CANCELLED','SUPPRESSED'].includes(status)?reason:entry[1]];
    if(reason&&!['BLOCKED','CANCELLED','SUPPRESSED'].includes(status)&&reason!==entry[1])lines.push(reason);
    if(Number.isInteger(item.read_attempts)&&item.read_attempts>=0)lines.push('已尝试读取 '+item.read_attempts+' 次。');
    if(item.read_error&&errors[item.read_error])lines.push('上次读取：'+errors[item.read_error]+'。');
    if(status==='OBSERVED'){
      if(result.fresh===true)lines.push('这次已取得新的来源报告；内容可能与上次相同。');
      else lines.push('尚未确认这份记录是否来自新的独立读取。');
    }
    if(status==='CANCELLED'&&result.observation_id)lines.push('停止前取得的来源记录仍已保留，不再据此推进原目标。');
    if(item.context_is_current===false)lines.push('这是当时取得的记录；原目标或依据已变化，需要重新判断对当前情况是否适用。');
    if(entry[2]&&status!=='RUNNING'&&options.workerEnabled===false)lines.push('后台当前未启动，这次读取仍在等待后台继续。');
    return {label:entry[0],status,lines,reports:reportsFor(item,options.graph),observedAt:typeof result.observed_at==='string'?result.observed_at:null,
      ongoing:entry[2],tone:entry[3],needsApproval:false,artifactLabel:'阅读完整调查计划'};
  }
  function decisionBasisLabel(basis) {
    return ({FORECAST:'依据：已形成的预测',OBSERVED_EVIDENCE:'依据：已经取得的来源报告',EPISTEMIC_GAP:'依据：尚缺少必要信息，先补足了解'})[basis]||'';
  }
  function hypothesisView(record,options={}) {
    const c=record.content||{},descriptive=record.node_type==='ProspectiveHypothesis';
    const description=descriptive?({NO_TARGET:'还没有具体、可观察的预测目标。',TARGET_CONTRACT_UNESTABLISHED:'还没有建立这个来源的可观察指标。',TARGET_VALUE_TYPE_MISMATCH:'预期值与来源指标的类型不匹配，未作为可计分预测。'})[c.unscored_reason]||'尚不能计分，不能作为当前事实。':record.node_type==='Prediction'?'这是关于后续观察的预测，并非已经发生的事实。':'尚未确认，不能作为当前事实。';
    return {label:descriptive?'描述性假设 · 尚不能计分':record.node_type==='Prediction'?(options.resolved?'先前预测 · 已与观察核对':'可核对的预测 · 等待后续观察'):'待核对的假设',description,
      probabilityLabel:descriptive?'主观估计，未计分且尚未校准':c.calibration?.includes('UNQUALIFIED')?'尚未校准，不能当作可靠把握':'估计依据与校准尚待核对'};
  }
  return Object.freeze({describe,decisionBasisLabel,hypothesisView});
});

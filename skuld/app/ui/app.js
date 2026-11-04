'use strict';

// The old app shell is retained; authorization and state come only from Runtime.
// Credentials are held only in this closure. No browser persistence is used.
(() => {
  const $ = id => document.getElementById(id);
  const state = { uid:'', key:'', epoch:0, viewVersion:0, currentView:'chat', session:crypto.randomUUID(), runtime:null, graph:null,
    refreshIssued:0, refreshApplied:0, graphFetchedAt:0, pendingChat:null, pendingCoding:null, chatBusy:false,
    codingBusy:false, loginBusy:false, approvals:new Map(), busyActions:new Set(), cancelling:new Set(),
    localTurns:new Map(), transcriptFingerprint:'', runtimeFingerprint:'', graphFingerprint:'',
    graphSelection:'', inspectorSelection:null, drawerReturnFocus:null, pollBusy:false, renderedGraph:null,
    projectDescriptor:null, projectRequestSerial:0, projectStepBusy:false, pendingProjectStep:null };
  const terminalRuns = new Set(['VERIFIED','FAILED','CANCELLED','UNKNOWN','PREVIEW_ONLY']);
  const runStatuses = {PENDING:'等待开始',RUNNING:'正在处理',VERIFIED:'独立检查通过 · 等待审阅',
    FAILED:'准备或检查失败',CANCELLED:'已取消',UNKNOWN:'结果尚未确认',PREVIEW_ONLY:'多文件补丁 · 仅供预览'};
  const runPhases = {SNAPSHOT:'准备隔离副本',BASELINE:'检查原始行为',PI_CODING:'Pi 正在修改代码',
    CHECK_SANDBOX_PREPARATION:'准备独立检查环境',CHECK_STARTING:'启动独立检查',CHECKING:'独立检查正在运行',
    REPAIRING:'根据检查失败准备一次修复',CANCELLING:'正在停止所属进程',STOPPED:'本次处理已停止',PREPARED:'补丁已准备'};
  const workStatuses = {PREPARED:'等待你检查',EXECUTED:'已执行 · 实际回执已留存',STALE:'依据已变 · 需要重新准备',RUNNING:'正在执行注册阶段',UNKNOWN:'结果未确认 · 禁止自动重跑'};
  const nodeNames = {Subject:'主体',Fact:'事实',Belief:'信念',Task:'任务',Commitment:'承诺',Goal:'目标',
    Prediction:'预测',PredictionError:'预测误差',Concern:'关注',Decision:'决定',Action:'行动',
    Boundary:'边界',Preference:'偏好',Relationship:'关系',Hypothesis:'假设',ProspectiveHypothesis:'描述性假设',Revision:'修订',
    CodingRun:'代码运行',VerificationResult:'独立检查',PatchArtifact:'补丁',ActionOutcome:'行动结果'};
  function text(node, value) { node.textContent = value == null ? '' : String(value); return node; }
  function el(tag, value, className) { const node=document.createElement(tag); if(value!=null)text(node,value); if(className)node.className=className; return node; }
  function paragraph(parent,value,className) { const node=el('p',value,className);parent.append(node);return node; }
  function errorText(error) {
    return String(error?.message || error || '请求失败。')
      .replace(/sk-[\w-]{12,}/gi,'[凭证已隐藏]')
      .replace(/Bearer\s+[^\s"'<>]+/gi,'Bearer [隐藏]')
      .replace(/(?:api_key|gateway_key|authorization|password|token)\s*[:=]\s*[^,\n]+/gi,'[私密字段已隐藏]')
      .replace(/(?:\/root\/|\/home\/|[A-Za-z]:[\\/])[^\s"'<>]+/g,'[主机路径已隐藏]').slice(0,600);
  }
  function setError(error) { text($('error'),errorText(error));$('error').hidden=false; }
  function clearError() { text($('error'),'');$('error').hidden=true; }
  function guard(fn, clear=true) {
    return async event => { if(event?.preventDefault)event.preventDefault(); if(clear)clearError();
      try { await fn(event); } catch(error) { if(!error.stale && (clear || $('error').hidden))setError(error); } };
  }
  function assertEpoch(epoch) { if(epoch!==state.epoch) { const error=new Error('旧连接的返回已丢弃。');error.stale=true;throw error; } }
  async function api(path, body, method) {
    const epoch=state.epoch, uid=state.uid, key=state.key;
    if(!uid||!key)throw new Error('请先连接你的主体。');
    const response=await fetch('/api/v1/subjects/'+encodeURIComponent(uid)+path, {
      method:method || (body?'POST':'GET'), cache:'no-store', credentials:'omit',
      headers:{Authorization:'Bearer '+key,'Content-Type':'application/json'}, body:body?JSON.stringify(body):undefined
    });
    assertEpoch(epoch);
    if(!response.ok) {
      let detail='请求未完成（HTTP '+response.status+'）。';
      try { const result=await response.json(); if(typeof result.detail==='string')detail=result.detail; } catch(_) {}
      assertEpoch(epoch); const error=new Error(detail); error.httpStatus=response.status;throw error;
    }
    const result=response.headers.get('content-type')?.includes('application/json')?await response.json():await response.text();
    assertEpoch(epoch);return result;
  }
  async function refreshAccepted(epoch,hintId,acceptedLabel,forceGraph=false) {
    try { await refresh(forceGraph);assertEpoch(epoch);return true; }
    catch(error) {
      assertEpoch(epoch);
      text($(hintId),acceptedLabel+'；当前状态刷新尚未确认，请刷新后查看已有记录。相同内容再次显式提交会核对同一个请求。');
      setError(error);return false;
    }
  }
  function fillSelect(id, items, placeholder) {
    const select=$(id),selected=select.value,desired=[{value:'',label:placeholder},...items];
    if(select.options.length===desired.length && desired.every((item,i)=>select.options[i].value===item.value && select.options[i].textContent===item.label))return;
    select.replaceChildren(); for(const item of desired){const option=el('option',item.label);option.value=item.value;select.append(option);}
    if(items.some(item=>item.value===selected))select.value=selected;
  }
  function fileLabel(value) { return typeof value==='string'&&!value.startsWith('/')&&!/^[a-z]:[\\/]/i.test(value)?value:'（主机路径已隐藏）'; }
  function recordText(node) { const c=node?.content||{};return String(c.text || c.goal || c.reason || c.name || c.title || c.expected_effect || node?.semantic_identity_key || node?.node_type || ''); }
  function short(value,length=80) { const s=String(value||'');return s.length>length?s.slice(0,length)+'…':s; }
  function timeLabel(value) { if(!value)return '';const date=new Date(value);return Number.isNaN(date.getTime())?String(value):date.toLocaleString('zh-CN',{hour12:false}); }
  function showView(view) {
    state.currentView=view;state.viewVersion++;
    for(const node of document.querySelectorAll('.workspace-view'))node.hidden=node.id!==view+'-view';
    for(const node of document.querySelectorAll('#workspace-nav [data-view]')){
      const active=node.dataset.view===view;node.classList.toggle('active',active);
      if(active)node.setAttribute('aria-current','page');else node.removeAttribute('aria-current');
    }
  }
  function drawerTab(tab) {
    for(const node of document.querySelectorAll('.cognition-panel'))node.hidden=node.id!==tab+'-panel';
    for(const node of document.querySelectorAll('[data-tab]')){const active=node.dataset.tab===tab;node.classList.toggle('active',active);node.setAttribute('aria-current',active?'page':'false');}
  }
  function openDrawer(tab='world') {
    drawerTab(tab);const drawer=$('cognition-drawer');
    if(!drawer.classList.contains('open'))state.drawerReturnFocus=document.activeElement;
    drawer.inert=false;drawer.setAttribute('aria-hidden','false');drawer.classList.add('open');
    $('drawer-scrim').hidden=false;$('app').inert=true;$('close-drawer').focus();
  }
  function closeDrawer() {
    const drawer=$('cognition-drawer');drawer.classList.remove('open');drawer.inert=true;drawer.setAttribute('aria-hidden','true');
    $('drawer-scrim').hidden=true;$('app').inert=false;
    if(state.drawerReturnFocus?.isConnected)state.drawerReturnFocus.focus();state.drawerReturnFocus=null;
  }
  function artifact(content,label) { text($('artifact'),content);text($('artifact-label'),label||'已选择');openDrawer('evidence'); }
  async function loadArtifact(id,label) { artifact(await api('/artifacts/'+encodeURIComponent(id)),label); }
  function actionButton(parent,label,fn,disabled=false,primary=false) {
    const button=el('button',label,primary?'primary':'');button.type='button';button.disabled=disabled;
    button.onclick=guard(async()=>{if(button.disabled)return;const epoch=state.epoch;button.disabled=true;
      try { await fn(); } finally { if(epoch===state.epoch && button.isConnected)button.disabled=disabled; } });
    parent.append(button);return button;
  }
  function renderTranscript(graph) {
    const turns=new Map();
    for(const source of graph.source_events||[]) {
      if(!['UserConversation','ConversationComplete'].includes(source.source_type))continue;
      const key=String(source.source_ref),turn=turns.get(key)||{id:key,at:source.received_at};
      if(source.source_type==='UserConversation')turn.user=source.content?.text;
      else { turn.reply=source.content?.result?.reply;turn.completeSource=source.source_event_id; }
      turns.set(key,turn);
    }
    for(const [key,local] of state.localTurns){const durable=turns.get(key);if(durable?.reply)state.localTurns.delete(key);else turns.set(key,{...local,...durable,reply:durable?.reply||local.reply});}
    const rows=[...turns.values()].filter(t=>typeof t.user==='string'||typeof t.reply==='string')
      .sort((a,b)=>String(a.at||'').localeCompare(String(b.at||''))).slice(-80);
    const fingerprint=JSON.stringify(rows);if(fingerprint===state.transcriptFingerprint)return;
    state.transcriptFingerprint=fingerprint;const scroll=$('chatlog'),atBottom=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<100;
    scroll.replaceChildren();
    if(!rows.length) { const empty=el('div',null,'chat-empty');empty.append(el('span','CONTINUITY / 主体持续性'),el('h2','有些事情，值得继续。'),el('p','告诉我你在意什么、答应了什么，以及还没有做完的事。'));scroll.append(empty); }
    for(const turn of rows){
      for(const [speaker,content] of [['user',turn.user],['assistant',turn.reply]]){
        if(typeof content!=='string')continue;const item=el('article',null,'chat-message '+speaker);item.dataset.turnId=turn.id;
        item.append(el('span',speaker==='user'?'YOU / 你':'SKULD'),el('p',content));if(turn.at)item.append(el('small',timeLabel(turn.at)));scroll.append(item);
      }
      if(!turn.reply)scroll.append(el('p',state.chatBusy&&state.pendingChat?.idempotency_key===turn.id?'Skuld 正在根据持续记录思考…':'这条消息尚无已确认的回复。','thinking'));
    }
    if(atBottom||state.chatBusy)scroll.scrollTop=scroll.scrollHeight;
  }
  function renderResponsibilities(runtime) {
    const target=$('responsibilities');target.replaceChildren();
    for(const node of (runtime.responsibilities||[]).slice(0,8)){
      const item=el('article',null,'responsibility');item.dataset.nodeId=node.node_id;
      item.append(el('p',recordText(node)),el('small',nodeNames[node.node_type]||node.node_type));target.append(item);
    }
    if(!target.childElementCount)paragraph(target,'还没有需要延续的事。可以先从对话里告诉我。','empty-note');
    const work=$('work-preview');work.replaceChildren();
    for(const item of [...(runtime.product_work||[]),...(runtime.work||[])].filter(w=>w.status==='PREPARED'||w.kind==='INVESTIGATE').slice(-3).reverse()){
      paragraph(work,short(item.title,90));if(item.kind==='INVESTIGATE')work.append(el('small',window.SkuldProductWork.describe(item,{workerEnabled:runtime.worker_enabled}).label,'investigation-preview-status'));
    }
    if(!work.childElementCount)paragraph(work,'有充分依据的下一步，会出现在这里。','empty-note');
    text($('worker-title'),runtime.worker_enabled===false?'记录会留在这里。':'事情在后台继续。');
    text($('worker-state'),runtime.worker?.error?'后台上次处理未确认，请查看实际回执。':runtime.worker?.last_tick?'最近一次后台推进：'+timeLabel(runtime.worker.last_tick):runtime.worker_enabled===false?'后台未启动；当前仅处理你在页面上的操作。':'还没有已确认的后台推进记录。');
    text($('subject-name'),runtime.owner_name||'你的 Skuld');text($('subject-mode'),runtime.simulation_only?'模拟验收主体':'私有主体');
    text($('connection-status'),runtime.worker?.error?'后台存在待核对记录':'持续连接 · '+(runtime.simulation_only?'模拟验收':'私有工作区'));
  }
  function renderProjectDescription() {
    const project=(state.runtime?.coding_projects||[]).find(p=>p.project_id===$('coding-project').value),target=$('project-description');target.replaceChildren();
    if(!project)return;
    if(project.description)paragraph(target,project.description);
    paragraph(target,'可修改文件：'+(project.allowed_edit_files||[]).map(fileLabel).join('、'));
    if(project.verification_description||project.check_description)paragraph(target,'独立检查：'+(project.verification_description||project.check_description));
    paragraph(target,publicationText(project));
    if(project.apply_scope)paragraph(target,'发布范围：'+(project.apply_scope==='SINGLE_FILE_ATOMIC'?'原注册项目中一个经过独立检查的文件；多文件修改可预览':project.apply_scope==='VERSIONED_SNAPSHOT_PUBLICATION'?'独立工作区版本':project.apply_scope));
  }
  function publicationText(project) {
    if(project?.publication_description||project?.publication_note)return project.publication_description||project.publication_note;
    if(project?.apply_scope==='VERSIONED_SNAPSHOT_PUBLICATION')return '批准后发布到 Skuld 工作区的独立版本；原项目仓库保持原样。';
    if(project?.apply_scope==='SINGLE_FILE_ATOMIC')return '批准后原子替换原注册项目中一个经过独立检查的文件；不会创建 Git commit。';
    return '生效范围由项目声明与实际执行回执决定，请审阅后再批准。';
  }
  function workProject(item,runtime) {
    const run=(runtime?.coding_runs||[]).find(run=>run.run_id===item.coding_run_id);
    return (runtime?.coding_projects||[]).find(project=>project.project_id===(run?.project_id||item.project_binding?.project_id));
  }
  function renderCoding(runtime) {
    const projects=runtime.coding_projects||[],responsibilities=runtime.responsibilities||[];
    fillSelect('coding-project',projects.map(p=>({value:p.project_id,label:p.name||p.project_id})),'选择已注册项目');
    fillSelect('coding-responsibility',responsibilities.map(n=>({value:n.node_id,label:short(recordText(n),150)})),'选择一件已有的事');
    renderProjectDescription();$('coding-prepare').disabled=state.codingBusy||!projects.length||!responsibilities.length;
    if(!state.codingBusy&&!state.pendingCoding)text($('coding-hint'),!projects.length?'这个主体还没有注册 coding 项目。':!responsibilities.length?'先在对话里留下需要持续推进的目标，再关联这次修改。':'修改在隔离副本中进行。具体补丁与生效范围，需要逐项批准。');
    const target=$('coding-runs');target.replaceChildren();const runs=runtime.coding_runs||[];text($('coding-count'),runs.length?runs.length+' 份运行记录':'');
    const workByAction=new Map((runtime.work||[]).map(w=>[w.action_id,w]));
    for(const run of runs){
      const card=el('article',null,'coding-run');card.dataset.runId=run.run_id;card.dataset.status=run.status;card.dataset.phase=run.phase||'';
      card.append(el('span',runStatuses[run.status]||'状态待核对','run-status'),el('h3',projects.find(p=>p.project_id===run.project_id)?.name||run.project_id));
      const registered=projects.find(p=>p.project_id===run.project_id);
      paragraph(card,workByAction.get(run.action_id)?.status==='EXECUTED'?(registered?.apply_scope==='VERSIONED_SNAPSHOT_PUBLICATION'?'已经发布独立工作区版本，实际回执已留存。':'具体补丁已经执行，实际回执已留存。'):runPhases[run.phase]||'请结合回执核对当前阶段。','muted');
      if(run.base_commit)paragraph(card,'基础版本：'+run.base_commit.slice(0,12),'coding-fingerprint');
      if(run.changed_files?.length)paragraph(card,'变更文件：'+run.changed_files.map(fileLabel).join('、'),'muted');
      if(Number.isFinite(run.actual_pi_model_calls))paragraph(card,'实际 Pi 模型请求：'+run.actual_pi_model_calls,'muted');
      const check=run.verification||{};
      if(Number.isFinite(check.exit_code))paragraph(card,'独立检查：'+(check.status==='PASSED'&&check.exit_code===0?'通过':'未通过')+' · 退出码 '+check.exit_code);
      if(run.failure_code)paragraph(card,'停止原因：'+run.failure_code,'muted');
      if(run.patch_sha256)paragraph(card,'补丁 SHA256：'+run.patch_sha256,'coding-fingerprint');
      const buttons=el('div',null,'action-buttons');card.append(buttons);
      if(run.patch_artifact_id)actionButton(buttons,'查看代码补丁',()=>loadArtifact(run.patch_artifact_id,'代码补丁 · '+run.run_id));
      actionButton(buttons,'查看运行依据',()=>{artifact(JSON.stringify(runSummary(run),null,2),'运行依据 · '+run.run_id);});
      if(run.status==='VERIFIED'&&run.action_id)actionButton(buttons,'审阅发布动作',()=>showView('actions'),false,true);
      if(run.status==='PREVIEW_ONLY')paragraph(card,'这份补丁保留预览与检查证据；当前不开放发布。','muted');
      if(run.status==='UNKNOWN')paragraph(card,'最终执行结果尚未确认；本次不会自动重跑或发布。请先核对已有回执。','muted');
      if(!terminalRuns.has(run.status))actionButton(buttons,'取消本次修改',async()=>{
        state.cancelling.add(run.run_id);renderCoding(state.runtime);try{await api('/coding/'+encodeURIComponent(run.run_id)+'/cancel',{});await refresh();}
        finally{state.cancelling.delete(run.run_id);if(state.runtime)renderCoding(state.runtime);}
      },state.cancelling.has(run.run_id)||run.phase==='CANCELLING');
      target.append(card);
    }
    if(!runs.length)paragraph(target,'尚未提交代码修改。项目、目标和验收要求明确后，就可以开始。','empty-note');
  }
  function descriptorRevision(descriptor) {
    const value=descriptor?.current_revision;return typeof value==='string'?value:typeof value?.revision_id==='string'?value.revision_id:'';
  }
  function descriptorSteps(descriptor) {
    const steps=descriptor?.steps||[];
    if(Array.isArray(steps))return steps.map(item=>typeof item==='string'?{id:item,label:item}:{id:item.step_id||item.id,label:item.name||item.title||item.step_id||item.id,description:item.description}).filter(item=>typeof item.id==='string');
    return Object.entries(steps).map(([id,item])=>({id,label:item?.name||item?.title||id,description:item?.description}));
  }
  function renderProjectSteps(runtime) {
    fillSelect('project-run-project',(runtime.coding_projects||[]).map(p=>({value:p.project_id,label:p.name||p.project_id})),'选择已注册项目');
    fillSelect('project-run-responsibility',(runtime.responsibilities||[]).map(n=>({value:n.node_id,label:short(recordText(n),150)})),'选择一件已有的事');
    const pid=$('project-run-project').value,descriptor=state.projectDescriptor?.project_id===pid?state.projectDescriptor:null,steps=descriptorSteps(descriptor),revision=descriptorRevision(descriptor);
    fillSelect('project-run-step',steps.map(item=>({value:item.id,label:item.label})),'选择一个已注册阶段');
    const panel=$('project-revision');panel.replaceChildren();panel.dataset.revisionId=revision;
    if(!pid)paragraph(panel,'请选择项目，读取实际工作区版本。');
    else if(!descriptor)paragraph(panel,'正在读取项目的实际工作区版本与注册阶段…');
    else {
      if(descriptor.description)paragraph(panel,descriptor.description);
      paragraph(panel,revision?'固定版本：'+revision:'这个项目尚无可执行的工作区版本。请先准备并发布经过独立检查的补丁。','coding-fingerprint');
      const step=steps.find(s=>s.id===$('project-run-step').value);if(step?.description)paragraph(panel,step.description);
      paragraph(panel,'执行只使用注册的阶段和所选固定版本；不会接受自由命令。');
    }
    $('project-step-prepare').disabled=state.projectStepBusy||!revision||!$('project-run-step').value||!$('project-run-responsibility').value;
  }
  async function refreshProjectDescriptor() {
    const pid=$('project-run-project').value;if(!pid){state.projectDescriptor=null;return;}
    const registered=state.runtime?.coding_projects?.find(project=>project.project_id===pid);
    if(registered?.apply_scope==='SINGLE_FILE_ATOMIC'){state.projectDescriptor={project_id:pid,steps:[],current_revision:null,description:'这个项目使用单文件原子应用，尚未提供版本化注册阶段入口。'};renderProjectSteps(state.runtime);return;}
    const request=++state.projectRequestSerial,descriptor=await api('/projects/'+encodeURIComponent(pid));
    if(request!==state.projectRequestSerial||$('project-run-project').value!==pid)return;
    state.projectDescriptor={...descriptor,project_id:pid};if(state.runtime)renderProjectSteps(state.runtime);
  }
  function validApproval(item) {
    const approval=state.approvals.get(item.action_id);
    return approval&&approval.actionHash===item.action_hash&&Date.parse(approval.expiresAt)>Date.now()?approval:null;
  }
  async function withAction(item, fn) {
    if(state.busyActions.has(item.action_id))return;const epoch=state.epoch;state.busyActions.add(item.action_id);renderWork(state.runtime);
    try { await fn(); } finally { if(epoch===state.epoch){state.busyActions.delete(item.action_id);if(state.runtime)renderWork(state.runtime);} }
  }
  function renderWork(runtime) {
    const target=$('work');target.replaceChildren();
    for(const item of runtime.work||[]){
      const card=el('article',null,'work');card.dataset.actionId=item.action_id;card.dataset.status=item.status;
      const coding=item.origin==='CODING'||Boolean(item.coding_run_id),project=Boolean(item.project_binding)||item.origin==='PROJECT_EXECUTION',busy=state.busyActions.has(item.action_id),approval=validApproval(item);
      if(!approval)state.approvals.delete(item.action_id);
      const executionStatuses={PROJECT_STEP_COMPLETED:'注册阶段运行通过 · 已留存实际输出',PROJECT_STEP_FAILED:'注册阶段运行失败 · 请查看实际输出',UNKNOWN:'执行结果尚未确认'};
      card.append(el('span',project&&item.status==='EXECUTED'?(executionStatuses[item.execution_status]||'执行已返回 · 结果待核对'):(workStatuses[item.status]||item.status),'run-status'),el('h3',item.title));
      paragraph(card,project?'只在固定工作区版本中运行所选注册阶段，留存实际退出码与输出。原责任是否完成需要另外确认。':coding?publicationText(workProject(item,runtime)):'这项动作会将准备好的方案保存到你的私有计划目录。','muted');
      if(project&&item.project_binding)paragraph(card,'项目：'+item.project_binding.project_id+' · 阶段：'+item.project_binding.step_id+' · 固定版本：'+item.project_binding.revision_id,'coding-fingerprint');
      if(item.evidence_ids?.length)paragraph(card,'证据结点：'+item.evidence_ids.join('、'),'coding-fingerprint');
      const buttons=el('div',null,'action-buttons');card.append(buttons);
      if(item.artifact_id)actionButton(buttons,project?'查看固定版本执行计划':coding?'查看待批准补丁':'查看草稿',()=>loadArtifact(item.artifact_id,project?'固定版本执行计划':coding?'待批准代码补丁':'已准备的草稿'));
      if(project&&item.execution_artifact_id)actionButton(buttons,'查看实际运行报告',()=>loadArtifact(item.execution_artifact_id,'实际阶段运行报告'),false,true);
      if(item.status==='PREPARED'){
        actionButton(buttons,approval?'已批准这项动作':'批准此动作',()=>withAction(item,async()=>{
          const response=await api('/actions/'+encodeURIComponent(item.action_id)+'/approve',{action_hash:item.action_hash});
          state.approvals.set(item.action_id,{id:response.approval_id,actionHash:item.action_hash,expiresAt:response.expires_at});
          artifact((project?'已批准这个固定版本的具体注册阶段。需要显式执行后，才会开始运行。':coding?'已批准这份具体补丁。'+publicationText(workProject(item,state.runtime)):'已批准这份具体方案，执行后保存到你的私有计划目录。')+'\n动作指纹：'+item.action_hash+'\n批准有效至：'+response.expires_at,'精确动作授权');
        }),busy||Boolean(approval),true);
        actionButton(buttons,project?'运行已批准阶段':coding?(workProject(item,runtime)?.apply_scope==='VERSIONED_SNAPSHOT_PUBLICATION'?'发布已批准补丁':'应用已批准补丁'):'执行已批准动作',()=>withAction(item,async()=>{
          const current=validApproval(item);if(!current)throw new Error('请先批准这一项具体动作，或重新批准已过期的授权。');
          const result=await api('/actions/'+encodeURIComponent(item.action_id)+'/execute',{approval_id:current.id});
          state.approvals.delete(item.action_id);await refresh(true);
          artifact('执行请求已返回，结果以持久回执为准。\n'+JSON.stringify(executionSummary(result),null,2),'实际执行回执');
        }),busy||!approval,true);
        actionButton(buttons,'撤销批准',()=>withAction(item,async()=>{
          const current=validApproval(item);if(!current)return;
          await api('/actions/'+encodeURIComponent(item.action_id)+'/revoke',{approval_id:current.id});state.approvals.delete(item.action_id);await refresh();
        }),busy||!approval);
      }
      actionButton(buttons,'有帮助',()=>withAction(item,async()=>{await api('/feedback',{action_id:item.action_id,useful:true,text:'这份准备对我有帮助。'});artifact('这次反馈已保存。相关的后续准备可以读取这条经验。','行动反馈');}),busy);
      actionButton(buttons,'没帮上忙',()=>withAction(item,async()=>{await api('/feedback',{action_id:item.action_id,useful:false,text:'这份准备没有帮上忙，需要调整。'});artifact('这次反馈已保存。相关的后续准备可以读取这条经验。','行动反馈');}),busy);
      target.append(card);
    }
    if(!target.childElementCount)paragraph(target,'还没有需要你批准的下一步。有充分依据时，准备好的内容会出现在这里。','empty-note');
  }
  function scalarFields(source,fields) {
    const result={};for(const key of fields){const value=source?.[key];if(['string','number','boolean'].includes(typeof value)||value===null)result[key]=value;}return result;
  }
  function runSummary(run) {
    return {...scalarFields(run,['run_id','project_id','status','phase','base_commit','patch_sha256','patch_artifact_id','action_id','actual_pi_model_calls','failure_code']),
      changed_files:(run.changed_files||[]).map(fileLabel),verification:{...scalarFields(run.verification,['status','exit_code','timed_out','stdout_sha256','stderr_sha256','source_manifest_sha256']),snapshot_manifest:Object.fromEntries(Object.entries(run.verification?.snapshot_manifest||{}).map(([name,hash])=>[fileLabel(name),hash]))}};
  }
  function executionSummary(result) {
    return {...scalarFields(result,['status','action_id','receipt_id','outcome_node_id','receipt_node_id','run_id','project_id','effect','effect_scope','sha256','patch_sha256','workspace_version_id','formal_repository_git_commit']),
      ...(result?.receipt?{receipt:scalarFields(result.receipt,['status','run_id','project_id','sha256','patch_sha256','check_result','effect_scope','workspace_version_id','formal_repository_git_commit'])}:{}),
      notice:'主机路径与私密配置不在界面显示。可以在持久图中核对行动结果。'};
  }
  function auditSummary(graph) {
    const types={};for(const node of graph.nodes||[])types[node.node_type]=(types[node.node_type]||0)+1;
    const receipts=(graph.nodes||[]).filter(n=>/Receipt|VerificationResult|ActionOutcome|CodingRun|PatchArtifact|RuntimeFailure/.test(n.node_type));
    return {subject_id:graph.subject_id,mode:graph.mode,version:graph.version,
      notice:'当前主体的持久图摘要；原始模型回执与执行证据由后台留存。界面隐藏主机路径及私密配置。',
      invariant_violations:(graph.invariant_violations||[]).length,node_count:(graph.nodes||[]).length,node_types:types,
      edge_count:(graph.edges||[]).length,source_event_count:(graph.source_events||[]).length,
      hyperedges:(graph.hyperedges||[]).map(h=>({hyperedge_id:h.hyperedge_id,relation_type:h.relation_type,status:h.status,source_event_id:h.source_event_id,participants:h.participants})),
      receipts:receipts.slice(-100).map(n=>({node_id:n.node_id,node_type:n.node_type,source_event_id:n.source_event_id,
        ...scalarFields(n.content,['status','stage','run_id','exit_code','timed_out','request_hash','response_hash','requested_model','served_model','actual_pi_model_calls','sha256','effect_scope','workspace_version_id','formal_repository_git_commit']),
        ...(n.content?.usage?{usage:scalarFields(n.content.usage,['prompt_tokens','completion_tokens','input_tokens','output_tokens','total_tokens','cost'])}:{}),
        ...(n.content?.connector_result?{connector_result:scalarFields(n.content.connector_result,['status','effect_scope','workspace_version_id','sha256','patch_sha256','check_result','formal_repository_git_commit'])}:{}),
        ...(n.content?.receipt?{receipt:scalarFields(n.content.receipt,['status','effect_scope','workspace_version_id','sha256','patch_sha256','formal_repository_git_commit'])}:{}),
        artifact_sha256:n.content?.sha256||n.content?.binding?.patch_sha256||null}))};
  }
  function renderFuture(graph) {
    const responsibility=$('future-responsibilities'),beliefs=$('future-beliefs');responsibility.replaceChildren();beliefs.replaceChildren();
    const current=state.runtime?.product_state;const sourceNodes=current?[...(current.goals||[]),...(current.facts||[]),...(current.predictions||[]),...(current.hypotheses||[])]:graph.nodes||[];
    const latestByKey=new Map();for(const n of sourceNodes){if(!['Goal','Task','Commitment','Belief','Fact','Prediction','Concern','Hypothesis','ProspectiveHypothesis'].includes(n.node_type))continue;const key=n.node_type+'|'+n.semantic_identity_key;const prior=latestByKey.get(key);if(!prior||String(n.created_at)>String(prior.created_at))latestByKey.set(key,n);}
    for(const node of [...latestByKey.values()].slice(-40).reverse()){
      const target=['Goal','Task','Commitment'].includes(node.node_type)?responsibility:beliefs,card=el('article');card.dataset.nodeId=node.node_id;
      const hypothesis=['Hypothesis','ProspectiveHypothesis','Prediction'].includes(node.node_type)?window.SkuldProductWork.hypothesisView(node,{resolved:(graph.nodes||[]).some(n=>n.node_type==='ForecastSettlement'&&n.content?.prediction_id===node.node_id&&n.content?.status==='RESOLVED')}):null;
      card.append(el('b',recordText(node)),el('small',hypothesis?.label||(nodeNames[node.node_type]||node.node_type)+' · '+(node.content?.status||node.modal_state||'记录')));
      if(hypothesis)paragraph(card,hypothesis.description);
      if(node.content?.due_at)paragraph(card,'约定时间：'+timeLabel(node.content.due_at));
      if(node.node_type==='Prediction' && node.content?.probability==null)paragraph(card,'预测概率尚未确认。');
      target.append(card);
    }
    if(!responsibility.childElementCount)paragraph(responsibility,'尚无已记录的目标或承诺。','empty-note');
    if(!beliefs.childElementCount)paragraph(beliefs,'尚无对应的判断与预测。','empty-note');
  }
  function renderSources(graph) {
    const target=$('source-list');target.replaceChildren();
    const observations=(graph.source_events||[]).filter(e=>/Observation|SourceFailure|External/.test(e.source_type)).slice(-12).reverse();
    for(const source of observations){const card=el('article',null,'source-observation');card.dataset.sourceEventId=source.source_event_id;
      card.append(el('span',source.source_type+' · '+(source.content?.title||source.content?.name||source.source_event_id)),el('small',timeLabel(source.received_at)));target.append(card);}
    if(!observations.length)paragraph(target,'持续观察产生的事实与来源记录，会保存在主体的世界模型中。','empty-note');
  }
  function svgElement(tag,attributes={},value) { const node=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [key,v] of Object.entries(attributes))node.setAttribute(key,String(v));if(value!=null)text(node,value);return node; }
  function inspectHyperedge(h) {
    state.inspectorSelection={kind:'hyperedge',id:h.hyperedge_id};const target=$('graph-inspector');target.replaceChildren();delete target.dataset.nodeId;target.dataset.hyperedgeId=h.hyperedge_id;
    target.append(el('h3',h.relation_type));paragraph(target,'关系 ID：'+h.hyperedge_id,'coding-fingerprint');
    if(h.status)paragraph(target,'状态：'+h.status);if(h.source_event_id)paragraph(target,'来源事件：'+h.source_event_id,'coding-fingerprint');
    const list=el('ul');for(const participant of h.participants||[]){const item=el('li',participant.role+' → '+participant.node_id);item.dataset.nodeId=participant.node_id;item.dataset.role=participant.role;list.append(item);}target.append(list);
  }
  function inspectNode(node,id) {
    state.inspectorSelection={kind:'node',id};const target=$('graph-inspector');target.replaceChildren();delete target.dataset.hyperedgeId;target.dataset.nodeId=id;
    target.append(el('h3',nodeNames[node?.node_type]||node?.node_type||'图中参与者'));paragraph(target,'结点 ID：'+id,'coding-fingerprint');
    if(!node){paragraph(target,'当前导出中缺少此结点内容。关系中的参与者引用仍按原始图展示。');return;}
    paragraph(target,recordText(node));if(node.source_event_id)paragraph(target,'来源事件：'+node.source_event_id,'coding-fingerprint');
    if(node.content?.status)paragraph(target,'状态：'+node.content.status);if(node.created_at)paragraph(target,'记录时间：'+timeLabel(node.created_at));
    const safe=scalarFields(node.content,['status','effect_scope','workspace_version_id','requested_model','served_model','sha256','exit_code','actual_pi_model_calls']);
    if(Object.keys(safe).length)target.append(el('pre',JSON.stringify(safe,null,2)));
  }
  function keyboardClick(group,fn) { group.addEventListener('click',fn);group.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();fn();}}); }
  function renderHypergraph(graph) {
    const all=[...(graph.hyperedges||[])].sort((a,b)=>String(a.created_at||'').localeCompare(String(b.created_at||'')));
    fillSelect('hyperedge-selection',all.map(h=>({value:h.hyperedge_id,label:h.relation_type+' · '+short(h.hyperedge_id,24)})),'最近的真实关系');
    state.graphSelection=$('hyperedge-selection').value;
    const selected=state.graphSelection?all.filter(h=>h.hyperedge_id===state.graphSelection):all.slice(-5),nodes=new Map((graph.nodes||[]).map(n=>[n.node_id,n]));
    const target=$('hypergraph');target.replaceChildren();
    if(!selected.length){paragraph(target,'当前主体尚无可展示的超关系。产生真实关系后，参与者与角色会显示在这里。','empty-note');$('graph-inspector').replaceChildren();return;}
    const participants=[...new Set(selected.flatMap(h=>(h.participants||[]).map(p=>p.node_id)))],height=Math.max(420,participants.length*62+60,selected.length*90+60),width=620;
    const svg=svgElement('svg',{viewBox:'0 0 '+width+' '+height,role:'img','aria-label':'当前主体的真实动态超图'});
    const positions=new Map(participants.map((id,i)=>[id,{x:470,y:45+i*(height-90)/Math.max(1,participants.length-1)}]));
    for(let i=0;i<selected.length;i++){
      const h=selected[i],x=150,y=45+i*(height-90)/Math.max(1,selected.length-1),centerY=selected.length===1?height/2:y;
      for(const p of h.participants||[]){const pos=positions.get(p.node_id);const line=svgElement('line',{x1:x+28,y1:centerY,x2:pos.x-31,y2:pos.y,class:'graph-link','data-hyperedge-id':h.hyperedge_id,'data-node-id':p.node_id,'data-role':p.role});svg.append(line);
        svg.append(svgElement('text',{x:x+75,y:centerY+(pos.y-centerY)*.3-5,class:'graph-role'},short(p.role,28)));}
      const group=svgElement('g',{class:'graph-hyperedge',tabindex:0,role:'button','aria-label':h.relation_type+' '+h.hyperedge_id,'data-hyperedge-id':h.hyperedge_id});
      group.append(svgElement('polygon',{points:[x+','+(centerY-28),(x+28)+','+centerY,x+','+(centerY+28),(x-28)+','+centerY].join(' ')}),svgElement('text',{x,y:centerY+46,'text-anchor':'middle'},short(h.relation_type,24)),svgElement('title',{},h.hyperedge_id));
      keyboardClick(group,()=>inspectHyperedge(h));svg.append(group);
    }
    for(const id of participants){const node=nodes.get(id),pos=positions.get(id),group=svgElement('g',{class:'graph-participant'+(node?.node_type==='Subject'?' owner':''),tabindex:0,role:'button','aria-label':(node?.node_type||'参与者')+' '+id,'data-node-id':id});
      group.append(svgElement('circle',{cx:pos.x,cy:pos.y,r:28}),svgElement('text',{x:pos.x,y:pos.y+4,'text-anchor':'middle'},short(nodeNames[node?.node_type]||node?.node_type||'引用',8)),svgElement('text',{x:pos.x+38,y:pos.y+4},short(id,14)),svgElement('title',{},id));
      keyboardClick(group,()=>inspectNode(node,id));svg.append(group);
    }
    target.append(svg);
    const prior=state.inspectorSelection;
    if(prior?.kind==='hyperedge'&&all.some(h=>h.hyperedge_id===prior.id))inspectHyperedge(all.find(h=>h.hyperedge_id===prior.id));
    else if(prior?.kind==='node'&&participants.includes(prior.id))inspectNode(nodes.get(prior.id),prior.id);
    else inspectHyperedge(selected[selected.length-1]);
  }
  function renderGraph(graph) {
    if(state.renderedGraph===graph)return;state.renderedGraph=graph;
    const fingerprint=JSON.stringify({subject:graph.subject_id,nodes:(graph.nodes||[]).map(n=>n.node_id),hyperedges:graph.hyperedges,sourceCount:graph.source_events?.length,violations:graph.invariant_violations});
    renderTranscript(graph);if(fingerprint===state.graphFingerprint)return;state.graphFingerprint=fingerprint;
    text($('graph-summary'),(graph.nodes||[]).length+' 个结点 · '+(graph.hyperedges||[]).length+' 条真实超关系 · '+(graph.source_events||[]).length+' 个来源事件');
    text($('graph-integrity'),graph.invariant_violations?.length?'发现 '+graph.invariant_violations.length+' 个待核对约束':'当前导出无约束违规');
    renderHypergraph(graph);renderFuture(graph);renderSources(graph);text($('audit'),JSON.stringify(auditSummary(graph),null,2));
  }
  async function refresh(forceGraph=false) {
    const epoch=state.epoch,request=++state.refreshIssued;
    const needGraph=Boolean(forceGraph)||!state.graph||Date.now()-state.graphFetchedAt>=30000;
    const [runtime,graph]=await Promise.all([api('/state'),needGraph?api('/export'):Promise.resolve(state.graph)]);assertEpoch(epoch);if(request<state.refreshApplied)return;
    state.refreshApplied=request;state.runtime=runtime;state.graph=graph;if(needGraph)state.graphFetchedAt=Date.now();
    // The route menu can be a user draft too: only update it when backend route changes.
    if($('route').dataset.activeRoute!==runtime.route){$('route').value=runtime.route;$('route').dataset.activeRoute=runtime.route;}
    const fingerprint=JSON.stringify(runtime);
    if(fingerprint!==state.runtimeFingerprint){state.runtimeFingerprint=fingerprint;renderResponsibilities(runtime);renderCoding(runtime);renderProjectSteps(runtime);renderWork(runtime);renderProduct(runtime,graph);}
    else { const anyExpired=[...state.approvals.keys()].some(id=>!validApproval((runtime.work||[]).find(w=>w.action_id===id)||{}));if(anyExpired)renderWork(runtime); }
    renderGraph(graph);renderProduct(runtime,graph);renderFuture(graph);text($('health'),'后台已连接 · '+(runtime.simulation_only?'模拟验收主体':'私有主体'));await refreshProjectDescriptor();
  }
  // Onboarding stays a draft until the user reviews and saves it to the graph.
  function freshOnboarding() { return {record:null,answers:{},index:0,phase:'choice',payload:null,preview:null,importSerial:0,busy:false,pending:null}; }
  let onboarding=freshOnboarding();
  function onboardingPhase(phase) {
    onboarding.phase=phase;
    for(const name of ['choice','import','questions','review'])$('onboarding-'+name).hidden=name!==phase;
    showView('onboarding');
  }
  async function openOnboarding(edit=false) {
    const epoch=state.epoch,viewVersion=state.viewVersion,record=await api('/onboarding');assertEpoch(epoch);if(viewVersion!==state.viewVersion)return;
    onboarding={...freshOnboarding(),record,answers:{...record.answers}};
    $('history-file').value='';$('import-preview').hidden=true;$('import-consent').checked=false;
    $('answer-processing-consent').checked=false;text($('import-status'),'');text($('onboarding-status'),'');
    if(edit&&record.completed)renderOnboardingReview();else onboardingPhase('choice');
  }
  function saveQuestionDraft() {
    const question=onboarding.record?.questions[onboarding.index];if(!question)return;
    const value=$('question-answer').value.trim();if(value)onboarding.answers[question.id]=value;else delete onboarding.answers[question.id];
  }
  function showQuestion(index=0) {
    const questions=onboarding.record?.questions||[];
    if(index>=questions.length){renderOnboardingReview();return;}
    onboarding.index=index;const q=questions[index];
    text($('question-number'),String(index+1).padStart(2,'0')+' / '+String(questions.length).padStart(2,'0'));
    const rail=$('question-track');rail.replaceChildren();questions.forEach((_,i)=>rail.append(el('i',null,i<=index?'reached':'')));
    text($('question-label'),q.label);text($('question-title'),q.question);text($('question-hint'),q.hint);
    $('question-answer').value=onboarding.answers[q.id]||'';$('question-answer').placeholder=q.placeholder||'';
    text($('question-next'),index===questions.length-1?'审阅起点 →':'继续 →');
    text($('question-back'),index===0?'← 返回':'← 上一个');onboardingPhase('questions');$('question-title').focus();
  }
  function renderOnboardingReview() {
    const target=$('onboarding-review-fields');target.replaceChildren();
    for(const q of onboarding.record?.questions||[]){
      const label=el('label'),heading=el('span',q.label),answer=el('textarea');answer.id='review-'+q.id;answer.dataset.dimension=q.id;
      answer.rows=2;answer.maxLength=4000;answer.value=onboarding.answers[q.id]||'';answer.placeholder='还不了解，可以留空';
      label.append(heading,answer);target.append(label);
    }
    const processing=onboarding.record?.processing||{};
    text($('onboarding-processing-note'),processing.available?'默认只保存在当前工作区。若想让模型据此理解你的处境、提出假设并准备下一步，可以明确允许处理以下当前回答。':'当前没有已启用的模型处理。你可以先保存、修改和重建这些记录；自然对话与主动生成准备需要配置并启用模型。');
    $('answer-consent-line').hidden=!processing.available;$('answer-processing-consent').checked=false;
    text($('answer-processing-label'),'允许将以上当前回答发送给 '+(processing.provider_host||'已配置的模型服务')+(processing.model?'（'+processing.model+'）':'')+'，用于对话与内部准备。');
    text($('onboarding-status'),'');onboardingPhase('review');
  }
  function importedMessages(conversation,container) {
    let shown=0;const rows=el('div',null,'import-messages'),controls=el('div',null,'action-buttons');container.append(rows,controls);
    function more(){
      const end=Math.min(shown+20,conversation.messages.length);
      for(;shown<end;shown++){
        const m=conversation.messages[shown],row=el('article',null,'import-message');
        row.append(el('small',(m.role==='user'?'你曾说':'历史助手回复')+' · '+(m.effective_at?timeLabel(m.effective_at):'历史时间未知')),
          el('p',m.text));
        if(m.role==='user'){
          const choose=el('div',null,'history-confirm-controls'),label=el('label','如今仍然适用？加入待审阅的：'),select=el('select');
          select.setAttribute('aria-label','把这句话归入哪个当前情况');
          for(const q of onboarding.record.questions){const option=el('option',q.label);option.value=q.id;select.append(option);}select.value='context';
          label.append(select);choose.append(label);
          actionButton(choose,'加入待审阅',()=>{
            const existing=onboarding.answers[select.value]||'';
            if(!existing.split('\n').includes(m.text)){const combined=[existing,m.text].filter(Boolean).join('\n');if(combined.length>4000)throw new Error('这段话超过当前回答的长度限制，请在问题或审阅页填写你仍愿意确认的部分。');onboarding.answers[select.value]=combined;}
            text($('import-status'),'已加入待审阅的当前情况；保存前可以修改或删除。历史本身没有变成当前事实。');
          });row.append(choose);
        }
        rows.append(row);
      }
      controls.replaceChildren();if(shown<conversation.messages.length)actionButton(controls,'再查看 20 条（还有 '+(conversation.messages.length-shown)+' 条）',more);
    }
    more();
  }
  function renderImportPreview(preview) {
    const target=$('import-conversations');target.replaceChildren();
    text($('import-counts'),preview.counts.conversations+' 段 · 你 '+preview.counts.user+' 条 / 助手 '+preview.counts.assistant+' 条');
    const warnings=$('import-warnings');warnings.replaceChildren();for(const value of preview.warnings)paragraph(warnings,value);
    for(const conversation of preview.conversations){
      const card=el('article',null,'import-conversation'),label=el('label',null,'import-select'),check=el('input');check.type='checkbox';check.dataset.conversationId=conversation.id;
      label.append(check,el('span',conversation.title));card.append(label);
      const details=el('details'),summary=el('summary','查看 '+conversation.messages.length+' 条历史消息');details.append(summary);
      let rendered=false;details.addEventListener('toggle',()=>{if(details.open&&!rendered){rendered=true;importedMessages(conversation,details);}});
      card.append(details);target.append(card);
    }
    $('import-consent').checked=false;$('import-preview').hidden=false;
  }
  function readableStatus(code) {
    return ({PENDING:'等待推进',RUNNING:'正在准备',PREPARED:'准备好了',BLOCKED:'尚不能推进',WAIT:'等待更有用的依据',STALE:'情况已改变',DONE:'已记录',FAILED:'需要核对'})[code]||'等待核对';
  }
  function renderProduct(runtime,graph=state.graph||{}) {
    const model=runtime.product_state||{},capability=runtime.capability_status||{};
    const note=$('product-capability');note.replaceChildren();
    const routes=runtime.model_options||[],hasRoute=routes.some(r=>r.route===runtime.route&&r.configured);
    const blocked=capability.prediction_first?.status==='BLOCKED';
    paragraph(note,blocked?'当前起点与历史已可在本工作区保存、重建；生成预测和内部准备尚未启用模型处理。':'已启用预测与内部准备处理；只有已允许使用的记录会进入模型。');
    if(!hasRoute)paragraph(note,'自然对话当前不可用：尚未配置可用模型。不会用预写回复替代实际对话。','form-hint');
    if(capability.differential_sec?.status==='BLOCKED')paragraph(note,'实际观察可以保留并核对预测；完整差分 SEC 学习尚未接通，不会把它显示成已经完成的能力。','form-hint');
    const target=$('product-current');target.replaceChildren();
    const sections=[['goals','想继续推进的事'],['relationships','重要的人与关系'],['facts','当前处境与限制'],['preferences','与你相处的方式'],['boundaries','你的界限']];
    for(const [key,title] of sections){
      const card=el('section',null,'world-card');card.append(el('h3',title));const records=model[key]||[];
      for(const record of records.slice(0,8)){paragraph(card,recordText(record));if(record.provider_eligible===false)card.append(el('small','仅保留在当前工作区'));}
      if(!records.length)paragraph(card,'还不了解，先留白。','empty-note');target.append(card);
    }
    if(model.closed_goals?.length){const card=el('section',null,'world-card');card.append(el('h3','已经完成或放下的事'));for(const record of model.closed_goals.slice(-6)){paragraph(card,recordText(record));card.append(el('small',record.content?.status==='completed'?'已经完成':'已取消或放下'));}target.append(card);}
    const predictions=$('product-predictions');predictions.replaceChildren();
    for(const record of [...(model.hypotheses||[]),...(model.predictions||[])].slice(-12).reverse()){
      const c=record.content||{},card=el('article',null,'product-item');
      const resolved=(graph.nodes||[]).some(n=>n.node_type==='ForecastSettlement'&&n.content?.prediction_id===record.node_id&&n.content?.status==='RESOLVED');
      const hypothesis=window.SkuldProductWork.hypothesisView(record,{resolved});
      card.append(el('span',hypothesis.label,'run-status'),el('h3',c.title||c.text||c.expected_effect||'待观察的变化'));
      paragraph(card,hypothesis.description,'muted');
      if(c.expected_effect)paragraph(card,c.expected_effect);
      if(c.horizon_end||c.observable?.window_end)paragraph(card,'观察窗口截至 '+timeLabel(c.horizon_end||c.observable.window_end),'muted');
      if(c.assumptions?.length)paragraph(card,'这依赖于：'+c.assumptions.join('；'),'muted');
      if(c.missing_evidence?.length)paragraph(card,'还不知道：'+c.missing_evidence.join('；'),'muted');
      if(c.probability!=null)paragraph(card,'估计 '+Math.round(c.probability*100)+'% · '+hypothesis.probabilityLabel,'muted');
      predictions.append(card);
    }
    if(!predictions.childElementCount)paragraph(predictions,'还没有已形成的未来判断。需要可处理的当前情况与目标，再进行真实推理。','empty-note');
    const candidates=$('product-candidates');candidates.replaceChildren();
    for(const record of (graph.nodes||[]).filter(n=>n.node_type==='ProductCandidate').slice(-8).reverse()){
      const c=record.content||{},card=el('article',null,'product-item');const activeGoals=new Set((model.goals||[]).map(n=>n.node_id));
      if((c.goal_ids||[]).some(id=>!activeGoals.has(id)))card.append(el('span','历史候选 · 目标已改变','run-status'));
      card.append(el('small',({PREPARE:'准备内容',ASK:'澄清一个问题',INVESTIGATE:'调查一项未知',WAIT:'暂时等待'})[c.kind]||'候选下一步'),el('h3',c.title||'待考虑的准备'));
      const basisLabel=window.SkuldProductWork.decisionBasisLabel(c.decision_basis);if(basisLabel)paragraph(card,basisLabel,'muted');
      paragraph(card,c.content);candidates.append(card);
    }
    if(!candidates.childElementCount)paragraph(candidates,'有足够依据后，这里会呈现不同的下一步。没有候选时不会假装已经在工作。','empty-note');
    const prepared=$('product-prepared');prepared.replaceChildren();
    for(const item of (runtime.product_work||[]).slice().reverse()){
      const presentation=window.SkuldProductWork.describe(item,{workerEnabled:runtime.worker_enabled,graph});
      const card=el('article',null,'product-item prepared-card');card.dataset.workStatus=presentation.status;card.dataset.statusTone=presentation.tone;
      card.append(el('span',presentation.label,'run-status'),el('h3',item.title));
      const basisLabel=window.SkuldProductWork.decisionBasisLabel(item.decision_basis);if(basisLabel)paragraph(card,basisLabel,'muted');
      if(item.kind==='INVESTIGATE'){
        const source=(graph.nodes||[]).find(n=>n.node_type==='SourceConfig'&&n.semantic_identity_key===item.source_id);
        if(source?.content?.name)paragraph(card,'来源：'+source.content.name,'investigation-source');
        const progress=el('div',null,'investigation-progress');
        for(const line of presentation.lines)paragraph(progress,line);
        if(presentation.observedAt)progress.append(el('small','读取记录时间：'+timeLabel(presentation.observedAt)));
        if(presentation.reports.length){const report=el('div',null,'investigation-report');report.append(el('small','来源当时报告'));for(const line of presentation.reports)paragraph(report,line);progress.append(report);}
        card.append(progress);
      }
      paragraph(card,(item.kind==='INVESTIGATE'?'调查计划：':'')+short(item.content,250),item.kind==='INVESTIGATE'?'investigation-plan':'');
      const buttons=el('div',null,'action-buttons');actionButton(buttons,presentation.artifactLabel,()=>loadArtifact(item.artifact_id,item.title),false,true);if(item.decision_id && !item.usefulness_feedback){
        const rate=async helpful=>{await api('/product/feedback',{decision_id:item.decision_id,helpful,text:'',idempotency_key:'product-rating:'+item.decision_id+':'+String(helpful)});await refresh();};
        actionButton(buttons,'有帮助',()=>rate(true),false);
        actionButton(buttons,'没帮上忙',()=>rate(false),false);
      }else if(item.usefulness_feedback){paragraph(card,item.usefulness_feedback.helpful?'已记录：有帮助':'已记录：没帮上忙','muted');}
      card.append(buttons);prepared.append(card);
    }
    if(!prepared.childElementCount)paragraph(prepared,'准备好的草稿、问题或调查计划会留在这里。当前还没有已完成的内容。','empty-note');
    const learning=$('product-learning');learning.replaceChildren();
    const settlements=(graph.nodes||[]).filter(n=>n.node_type==='ForecastSettlement').slice(-8).reverse();
    for(const record of settlements){const c=record.content||{},card=el('article',null,'product-item');
      card.append(el('b',c.status==='RESOLVED'?'已与实际观察核对':'观察已保留，尚不能用于结算'));
      if(c.status==='RESOLVED')paragraph(card,c.outcome?'这次实际来源报告与先前预期一致。':'这次实际来源报告与先前预期不同。');
      else paragraph(card,'来源、时间窗口或观察条件不匹配。未知结果仍保留为未知。');
      if(c.known_at)card.append(el('small',timeLabel(c.known_at)));learning.append(card);
    }
    const experiences=(graph.nodes||[]).filter(n=>n.node_type==='ForecastExperience'&&n.content?.parameter_update);
    if(experiences.length)paragraph(learning,'已有 '+experiences.length+' 次实际结果进入对应来源的参数更新。是否提高后续判断质量，仍需验证。','form-hint');
    if(!learning.childElementCount)paragraph(learning,'还没有可以核对的实际结果。没有观察到，不代表成功或失败。','empty-note');
    const history=$('product-history');history.replaceChildren();const episodes=model.episodes||[];
    text($('history-summary-label'),'此前的对话片段 · 当前重建包含 '+episodes.length+' 条');
    for(const record of episodes.slice().reverse()){
      const c=record.content||{},card=el('article',null,'import-message');card.append(el('small',(c.speaker==='ASSISTANT'||record.node_type==='AssistantTurn'?'助手':'你')+(c.historical?' · 导入的历史':' · 此前对话')),
        el('p',c.text||c.reply||''));history.append(card);
    }
    if(!history.childElementCount)paragraph(history,'还没有可展示的历史片段。','empty-note');
    // Provider choices come from the server's public configuration, never guessed defaults.
    const routeSelect=$('route'),options=routes.length?routes:[{route:runtime.route,label:'未配置模型'}];
    if(JSON.stringify(options)!==routeSelect.dataset.options){const current=routeSelect.value;routeSelect.replaceChildren();for(const option of options){const node=el('option',option.label||option.route);node.value=option.route;routeSelect.append(node);}routeSelect.value=options.some(o=>o.route===current)?current:runtime.route;routeSelect.dataset.options=JSON.stringify(options);}
    $('switch').disabled=routes.length<2;
    $('send').disabled=state.chatBusy||!hasRoute;
    if(!hasRoute&&!state.chatBusy)text($('chat-hint'),'当前未配置可用模型。你的起点和历史仍可保存、修改与查看。');
  }
  $('choose-import').onclick=()=>onboardingPhase('import');$('choose-questions').onclick=()=>showQuestion(0);
  $('onboarding-later').onclick=()=>{if(onboarding.phase==='questions')saveQuestionDraft();showView('chat');};
  $('import-back').onclick=()=>onboardingPhase('choice');$('import-skip').onclick=()=>showQuestion(0);
  $('question-form').onsubmit=event=>{event.preventDefault();saveQuestionDraft();showQuestion(onboarding.index+1);};
  $('question-skip').onclick=()=>{delete onboarding.answers[onboarding.record.questions[onboarding.index].id];showQuestion(onboarding.index+1);};
  $('question-back').onclick=()=>{saveQuestionDraft();if(onboarding.index===0)onboardingPhase('choice');else showQuestion(onboarding.index-1);};
  $('review-back').onclick=()=>{for(const node of document.querySelectorAll('#onboarding-review-fields textarea')){if(node.value.trim())onboarding.answers[node.dataset.dimension]=node.value.trim();else delete onboarding.answers[node.dataset.dimension];}showQuestion(7);};
  $('history-file').onchange=()=>{onboarding.importSerial++;onboarding.payload=null;onboarding.preview=null;$('import-preview').hidden=true;text($('import-status'),'');};
  $('import-preview-form').onsubmit=guard(async()=>{
    if(onboarding.busy)return;const file=$('history-file').files[0];if(!file)throw new Error('请先选择一份 JSON 导出文件。');
    if(file.size>10*1024*1024)throw new Error('文件超过 10 MiB，请先缩小到相关对话。');
    const epoch=state.epoch,viewVersion=state.viewVersion,active=onboarding,importSerial=onboarding.importSerial;active.busy=true;$('preview-import').disabled=true;
    try{const raw=await file.text();assertEpoch(epoch);if(importSerial!==active.importSerial)return;let payload;try{payload=JSON.parse(raw);}catch(_){throw new Error('文件不是有效 JSON；请选择 ChatGPT 或 Claude 的 JSON 导出。');}
      assertEpoch(epoch);const preview=await api('/imports/preview',{payload});assertEpoch(epoch);if(active!==onboarding||importSerial!==active.importSerial)return;
      active.payload=payload;active.preview=preview;if(viewVersion!==state.viewVersion)return;renderImportPreview(preview);text($('import-status'),'预览完成，还没有保存。请选择想保留的对话。');
    }catch(error){if(importSerial===active.importSerial)throw error;}finally{if(epoch===state.epoch&&active===onboarding){active.busy=false;$('preview-import').disabled=false;}}
  });
  $('commit-import').onclick=guard(async()=>{
    if(onboarding.busy)return;if(!onboarding.preview)throw new Error('请先预览文件。');
    const ids=[...document.querySelectorAll('#import-conversations input:checked')].map(n=>n.dataset.conversationId);
    if(!ids.length)throw new Error('请勾选至少一段对话。');if(!$('import-consent').checked)throw new Error('请确认把所选历史保存在当前工作区。');
    const epoch=state.epoch,viewVersion=state.viewVersion,active=onboarding,importSerial=onboarding.importSerial;active.busy=true;$('commit-import').disabled=true;
    try{const result=await api('/imports',{payload:active.payload,preview_digest:active.preview.digest,conversation_ids:ids,local_storage_consent:true});assertEpoch(epoch);if(active!==onboarding)return;
      text($('import-status'),'已保留 '+result.imported_messages+' 条历史消息，外部模型没有收到这些历史。');
      if(await refreshAccepted(epoch,'import-status','所选历史已经保存',true)){if(importSerial!==active.importSerial){text($('import-status'),'之前所选的历史已保存。你刚选择了另一份文件，请先预览新的文件。');return;}active.payload=null;active.preview=null;$('history-file').value='';if(viewVersion===state.viewVersion){if(Object.keys(active.answers).length)renderOnboardingReview();else showQuestion(0);}}
    }finally{if(epoch===state.epoch&&active===onboarding){active.busy=false;$('commit-import').disabled=false;}}
  });
  $('onboarding-save-form').onsubmit=guard(async()=>{
    if(onboarding.busy)return;const answers={};for(const node of document.querySelectorAll('#onboarding-review-fields textarea'))if(node.value.trim())answers[node.dataset.dimension]=node.value.trim();
    const processing_consent=$('answer-processing-consent').checked?{route:onboarding.record.processing.route,route_identity:onboarding.record.processing.route_identity,allow:true}:null;
    const signature=JSON.stringify({answers,processing_consent});if(!onboarding.pending||onboarding.pending.signature!==signature)onboarding.pending={signature,idempotency_key:crypto.randomUUID()};
    const epoch=state.epoch,viewVersion=state.viewVersion,active=onboarding,fields=[...document.querySelectorAll('#onboarding-review-fields textarea')];active.busy=true;for(const field of fields)field.disabled=true;$('answer-processing-consent').disabled=true;$('save-onboarding').disabled=true;text($('onboarding-status'),'正在保存你确认的起点…');
    try{const result=await api('/onboarding',{answers,idempotency_key:active.pending.idempotency_key,processing_consent});assertEpoch(epoch);if(active!==onboarding)return;
      if(viewVersion===state.viewVersion)active.answers={...result.answers};active.record={...active.record,...result};
      if(await refreshAccepted(epoch,'onboarding-status','起点已经保存',true)){active.pending=null;if(viewVersion===state.viewVersion)showView('continuity');}
    }finally{if(epoch===state.epoch&&active===onboarding){active.busy=false;for(const field of fields)field.disabled=false;$('answer-processing-consent').disabled=false;$('save-onboarding').disabled=false;}}
  });
  $('edit-start').onclick=guard(()=>openOnboarding(true));

  function resetConnection() {
    onboarding=freshOnboarding();
    for(const id of ['import-conversations','import-warnings','onboarding-review-fields','product-current','product-predictions','product-candidates','product-learning','product-history','product-prepared'])$(id).replaceChildren();
    $('history-file').value='';$('question-answer').value='';$('answer-processing-consent').disabled=false;$('import-consent').checked=false;$('answer-processing-consent').checked=false;$('import-preview').hidden=true;
    for(const phase of ['choice','import','questions','review'])$('onboarding-'+phase).hidden=phase!=='choice';
    for(const id of ['preview-import','commit-import','save-onboarding'])$(id).disabled=false;
    state.epoch++;state.uid='';state.key='';state.runtime=null;state.graph=null;state.session=crypto.randomUUID();state.pendingChat=null;state.pendingCoding=null;state.chatBusy=false;state.codingBusy=false;
    state.approvals.clear();state.busyActions.clear();state.cancelling.clear();state.localTurns.clear();state.refreshIssued=0;state.refreshApplied=0;state.graphFetchedAt=0;state.transcriptFingerprint='';state.runtimeFingerprint='';state.graphFingerprint='';state.renderedGraph=null;state.inspectorSelection=null;state.projectDescriptor=null;state.projectRequestSerial++;state.projectStepBusy=false;state.pendingProjectStep=null;
    closeDrawer();$('app').hidden=true;$('login').hidden=false;$('key').value='';$('message').value='';$('sourceurl').value='';$('sourcename').value='';$('coding-instruction').value='';$('coding-project').value='';$('coding-responsibility').value='';$('hyperedge-selection').value='';
    for(const id of ['responsibilities','work','work-preview','coding-runs','future-responsibilities','future-beliefs','hypergraph','graph-inspector','source-list','project-description'])$(id).replaceChildren();
    $('chatlog').replaceChildren();text($('artifact'),'从下一步或代码运行中，选择一份草稿或补丁。');text($('artifact-label'),'尚未选择');text($('audit'),'等待连接你的主体。');delete $('route').dataset.activeRoute;
    $('send').disabled=false;$('coding-prepare').disabled=true;$('project-run-project').value='';$('project-run-step').value='';$('project-run-responsibility').value='';$('project-step-prepare').disabled=true;text($('project-revision'),'请选择项目，读取实际工作区版本。');
  }
  $('login-form').onsubmit=guard(async()=>{
    if(state.loginBusy)return;
    const uid=$('uid').value.trim(),key=$('key').value.trim();if(!uid||!key)throw new Error('请输入用户 ID 与访问凭证。');
    resetConnection();state.uid=uid;state.key=key;const epoch=state.epoch;state.loginBusy=true;$('connect').disabled=true;
    try{await refresh();assertEpoch(epoch);$('login').hidden=true;$('app').hidden=false;$('key').value='';const start=await api('/onboarding');assertEpoch(epoch);if(!start.completed){await openOnboarding();}else{onboarding.record=start;onboarding.answers={...start.answers};showView('chat');$('message').focus();}}
    catch(error){if(epoch===state.epoch)resetConnection();throw error;}finally{state.loginBusy=false;$('connect').disabled=false;}
  });
  $('disconnect').onclick=()=>{resetConnection();clearError();$('uid').focus();};
  $('chat-form').onsubmit=guard(async()=>{
    if(state.chatBusy)return;const value=$('message').value.trim();if(!value)return;const epoch=state.epoch;
    if(!state.pendingChat||state.pendingChat.uid!==state.uid||state.pendingChat.text!==value)state.pendingChat={uid:state.uid,text:value,idempotency_key:crypto.randomUUID(),session_id:state.session};
    const request={text:value,idempotency_key:state.pendingChat.idempotency_key,session_id:state.pendingChat.session_id};
    state.localTurns.set(request.idempotency_key,{id:request.idempotency_key,user:value,at:new Date().toISOString()});state.chatBusy=true;state.transcriptFingerprint='';renderTranscript(state.graph||{});$('message').value='';$('send').disabled=true;text($('chat-hint'),'正在根据持续记录准备回复…');
    let accepted=false;
    try{const result=await api('/chat',request);assertEpoch(epoch);accepted=true;const local=state.localTurns.get(request.idempotency_key);if(local)local.reply=result.reply;state.transcriptFingerprint='';renderTranscript(state.graph||{});text($('chat-hint'),result.replayed?'已恢复同一份对话结果。':'这次对话已经留下可延续的记录。');if(await refreshAccepted(epoch,'chat-hint','这次对话已记录，回复已收到',true))state.pendingChat=null;}
    catch(error){if(epoch===state.epoch&&!accepted){if(!$('message').value)$('message').value=value;const rejected=error.httpStatus>=400&&error.httpStatus<500&&error.httpStatus!==408;if(rejected)state.pendingChat=null;text($('chat-hint'),rejected?'这次消息未被接受，请核对错误。':'回复尚未确认。再次发送相同内容，会使用同一个请求。');}throw error;}
    finally{if(epoch===state.epoch){state.chatBusy=false;$('send').disabled=false;state.transcriptFingerprint='';renderTranscript(state.graph||{});}}
  });
  $('message').addEventListener('keydown',event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();$('chat-form').requestSubmit();}});
  $('coding-form').onsubmit=guard(async()=>{
    if(state.codingBusy)return;const project_id=$('coding-project').value,responsibility_id=$('coding-responsibility').value,instruction=$('coding-instruction').value.trim();
    if(!project_id||!responsibility_id||!instruction)throw new Error('请先选择项目、关联一件已有的事，并填写修改要求。');
    const epoch=state.epoch,signature=JSON.stringify({uid:state.uid,project_id,responsibility_id,instruction});
    if(!state.pendingCoding||state.pendingCoding.signature!==signature)state.pendingCoding={signature,request:{project_id,responsibility_id,instruction,idempotency_key:crypto.randomUUID()}};
    state.codingBusy=true;$('coding-prepare').disabled=true;text($('coding-hint'),'正在提交。任务进入后台后，对话结束仍会继续处理。');
    let accepted=false;
    try{const result=await api('/coding',state.pendingCoding.request);assertEpoch(epoch);accepted=true;text($('coding-hint'),result.replayed?'已恢复同一份代码运行。':'已记录这次修改，请查看实际运行阶段、检查与补丁。');if(await refreshAccepted(epoch,'coding-hint','代码运行已记录'))state.pendingCoding=null;}
    catch(error){if(epoch===state.epoch&&!accepted){if(error.httpStatus>=400&&error.httpStatus<500&&error.httpStatus!==408)state.pendingCoding=null;text($('coding-hint'),state.pendingCoding?'请求结果尚未确认。再次显式提交相同内容，会核对同一份运行。':'请求未被接受，请核对项目、责任和错误信息。');}throw error;}
    finally{if(epoch===state.epoch){state.codingBusy=false;$('coding-prepare').disabled=!state.runtime?.coding_projects?.length||!state.runtime?.responsibilities?.length;}}
  });
  $('coding-project').onchange=renderProjectDescription;
  $('project-run-project').onchange=guard(async()=>{state.projectDescriptor=null;state.projectRequestSerial++;renderProjectSteps(state.runtime);await refreshProjectDescriptor();});
  $('project-run-step').onchange=()=>renderProjectSteps(state.runtime);$('project-run-responsibility').onchange=()=>renderProjectSteps(state.runtime);
  $('project-step-form').onsubmit=guard(async()=>{
    if(state.projectStepBusy)return;const project_id=$('project-run-project').value,step_id=$('project-run-step').value,responsibility_id=$('project-run-responsibility').value,revision_id=descriptorRevision(state.projectDescriptor);
    if(!project_id||!step_id||!responsibility_id||!revision_id||state.projectDescriptor?.project_id!==project_id)throw new Error('请选择项目的实际工作区版本、注册阶段与已有责任。');
    const epoch=state.epoch,signature=JSON.stringify({uid:state.uid,project_id,step_id,responsibility_id,revision_id});
    if(!state.pendingProjectStep||state.pendingProjectStep.signature!==signature)state.pendingProjectStep={signature,request:{revision_id,responsibility_id,idempotency_key:crypto.randomUUID()}};
    state.projectStepBusy=true;$('project-step-prepare').disabled=true;text($('project-step-hint'),'正在准备固定版本执行计划；这一步不会开始运行。');
    let accepted=false;
    try{const result=await api('/projects/'+encodeURIComponent(project_id)+'/steps/'+encodeURIComponent(step_id)+'/prepare',state.pendingProjectStep.request);assertEpoch(epoch);accepted=true;text($('project-step-hint'),result.replayed?'已恢复同一份执行计划。请审阅后单独批准与执行。':'执行计划已准备，请在下一步中审阅、批准并显式运行。');if(await refreshAccepted(epoch,'project-step-hint','执行计划已记录')){state.pendingProjectStep=null;showView('actions');}}
    catch(error){if(epoch===state.epoch&&!accepted){if(error.httpStatus>=400&&error.httpStatus<500&&error.httpStatus!==408)state.pendingProjectStep=null;text($('project-step-hint'),state.pendingProjectStep?'准备结果尚未确认，再次提交相同内容会核对同一个计划。':'执行计划未被接受，请核对当前版本与注册阶段。');}throw error;}
    finally{if(epoch===state.epoch){state.projectStepBusy=false;if(state.runtime)renderProjectSteps(state.runtime);}}
  });
  $('switch').onclick=guard(async()=>{const epoch=state.epoch;$('switch').disabled=true;try{await api('/model',{route:$('route').value});await refresh();}finally{if(epoch===state.epoch)$('switch').disabled=false;}});
  $('source-form').onsubmit=guard(async()=>{const epoch=state.epoch;$('watch').disabled=true;try{await api('/sources',{url:$('sourceurl').value.trim(),name:$('sourcename').value.trim(),adapter:$('adapter').value,poll_seconds:30});text($('source-hint'),'已连接这个来源，后台将持续观察。');await refresh();}finally{if(epoch===state.epoch)$('watch').disabled=false;}});
  $('export').onclick=guard(async()=>{const graph=await api('/export');state.graph=graph;renderGraph(graph);text($('audit'),JSON.stringify(auditSummary(graph),null,2));});
  $('refresh-graph').onclick=guard(refresh);$('hyperedge-selection').onchange=()=>{state.inspectorSelection=null;if(state.graph)renderHypergraph(state.graph);};
  for(const button of document.querySelectorAll('[data-view]'))button.onclick=guard(async()=>{if(button.dataset.view==='onboarding'){if(onboarding.record){showView('onboarding');}else await openOnboarding(true);}else showView(button.dataset.view);});
  for(const button of document.querySelectorAll('[data-drawer]'))button.onclick=guard(async()=>{openDrawer(button.dataset.drawer);await refresh(true);});
  for(const button of document.querySelectorAll('[data-tab]'))button.onclick=()=>drawerTab(button.dataset.tab);
  document.querySelector('.app-sidebar .mark').onclick=event=>{event.preventDefault();showView('chat');};
  $('mobile-context').onclick=()=>openDrawer('future');$('close-drawer').onclick=closeDrawer;$('drawer-scrim').onclick=closeDrawer;
  document.addEventListener('keydown',event=>{
    if(!$('cognition-drawer').classList.contains('open'))return;if(event.key==='Escape'){event.preventDefault();closeDrawer();return;}
    if(event.key==='Tab'){const nodes=[...$('cognition-drawer').querySelectorAll('button:not(:disabled),select,[tabindex="0"]')].filter(n=>n.getClientRects().length);const first=nodes[0],last=nodes[nodes.length-1];
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}
  });
  setInterval(async()=>{if(!state.uid||!state.key||state.pollBusy)return;state.pollBusy=true;try{await guard(refresh,false)();}finally{state.pollBusy=false;}},5000);
  fetch('/health',{credentials:'omit',cache:'no-store'}).then(r=>{if(!r.ok)throw new Error();return r.json();}).then(result=>text($('health'),result.real_user_gate==='CLOSED'?'后台已连接 · 真实用户入口关闭':'后台已连接')).catch(()=>text($('health'),'后台暂时不可用。'));
})();

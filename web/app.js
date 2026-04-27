document.addEventListener('DOMContentLoaded', () => {
    const elImport = document.getElementById('btn-import');
    const elPlay = document.getElementById('btn-play');
    const elStop = document.getElementById('btn-stop');
    const elVideoFeed = document.getElementById('video-feed');
    const elVideoPlaceholder = document.getElementById('no-video-placeholder');
    const elVideoInfo = document.getElementById('video-info');
    const elHardStatus = document.getElementById('hardware-status');
    const elTimeCur = document.getElementById('time-current');
    const elTimeElapsed = document.getElementById('time-elapsed');
    const elProgressBarInput = document.getElementById('progress-bar-input');
    const elNext = document.getElementById('btn-next');
    const elEventsCont = document.getElementById('events-container');
    const elEventsEmpty = document.getElementById('events-empty');
    const elEventCount = document.getElementById('event-count');
    const elPlaylist = document.getElementById('playlist-container');

    let isDraggingProgress = false;

    let state = {
        isRunning: false,
        isPaused: false,
        videoPath: null,
        lastEventId: -1,
        totalEvents: 0
    };

    // 绑定设置变更时同步到后端
    const sendConfigUpdate = async () => {
        if (!state.isRunning && !state.isPaused) return;
        const config = {
            model: document.getElementById('config-model').value,
            target: document.getElementById('config-target').value,
            strategy: document.getElementById('config-strategy').value,
            speed: document.getElementById('config-speed').value,
            smart: document.getElementById('config-smart').checked,
            save: document.getElementById('config-save').checked,
            drawbox: document.getElementById('config-drawbox').checked,
            use_gpu: document.getElementById('config-gpu').checked
        };
        await fetch('/api/action', {
            method: 'POST',
            body: JSON.stringify({ action: 'update_config', config: config })
        });
    };

    ['config-model', 'config-target', 'config-strategy', 'config-speed', 'config-smart', 'config-save', 'config-drawbox', 'config-gpu'].forEach(id => {
        document.getElementById(id).addEventListener('change', sendConfigUpdate);
    });

    // 初始化时开始获取状态
    setInterval(fetchStatus, 1000);
    setInterval(fetchEvents, 2000);

    // 绑定按钮
    elImport.addEventListener('click', async () => {
        const resp = await fetch('/api/action', {
            method: 'POST',
            body: JSON.stringify({ action: 'import' })
        });
        const data = await resp.json();
        if (data.success && data.video) {
            state.videoPath = data.video;
            elVideoInfo.innerText = "已就绪: " + data.filename;
            
            alert("视频导入成功！首帧画面已载入。");
            
            // 首帧已在后端加载，延时显示
            setTimeout(() => {
                elVideoPlaceholder.style.display = 'none';
                elVideoFeed.style.display = 'block';
                elVideoFeed.src = '/video_feed?t=' + Date.now();
            }, 500);
        }
    });

    elPlay.addEventListener('click', async () => {
        if (!state.videoPath && !state.isRunning && !state.isPaused) {
            alert("请先导入视频！");
            return;
        }
        
        if (state.isRunning && !state.isPaused) {
            await fetch('/api/action', {
                method: 'POST',
                body: JSON.stringify({ action: 'pause' })
            });
        } else {
            const config = {
                model: document.getElementById('config-model').value,
                target: document.getElementById('config-target').value,
                strategy: document.getElementById('config-strategy').value,
                speed: document.getElementById('config-speed').value,
                smart: document.getElementById('config-smart').checked,
                save: document.getElementById('config-save').checked,
                drawbox: document.getElementById('config-drawbox').checked,
                use_gpu: document.getElementById('config-gpu').checked
            };

            await fetch('/api/action', {
                method: 'POST',
                body: JSON.stringify({ action: 'play', config: config })
            });
        }
    });


    elStop.addEventListener('click', async () => {
        await fetch('/api/action', {
            method: 'POST',
            body: JSON.stringify({ action: 'stop' })
        });
        elVideoFeed.src = 'placeholder.jpg'; // 掐断流 (使用本地素材)
        elVideoPlaceholder.style.display = 'flex';
        elVideoInfo.innerText = "已停止";
        elProgressBarInput.value = 0;
    });

    if (elNext) {
        elNext.addEventListener('click', async () => {
            await fetch('/api/action', {
                method: 'POST',
                body: JSON.stringify({ action: 'next' })
            });
        });
    }

    // 进度条拖拽事件
    elProgressBarInput.addEventListener('mousedown', () => { isDraggingProgress = true; });
    elProgressBarInput.addEventListener('touchstart', () => { isDraggingProgress = true; }, {passive: true});
    
    const sendSeek = async () => {
        if (!state.videoPath) {
            elProgressBarInput.value = 0;
            return;
        }
        await fetch('/api/action', {
            method: 'POST',
            body: JSON.stringify({ action: 'seek', progress: parseFloat(elProgressBarInput.value) })
        });
        isDraggingProgress = false;
    };
    
    elProgressBarInput.addEventListener('mouseup', sendSeek);
    elProgressBarInput.addEventListener('touchend', sendSeek);

    async function fetchStatus() {
        try {
            const resp = await fetch('/api/status');
            const data = await resp.json();
            
            elHardStatus.innerText = data.hardware || "就绪";
            elTimeCur.innerText = data.timeStr || "00:00:00 / 00:00:00";
            elTimeElapsed.innerText = "耗时: " + (data.elapsed || "0s");
            
            if (!isDraggingProgress) {
                elProgressBarInput.value = data.progress || 0;
            }

            state.isRunning = data.isRunning;
            state.isPaused = data.isPaused;

            if (data.hasGpu !== undefined) {
                const gpuBox = document.getElementById('config-gpu');
                const gpuLabel = document.getElementById('label-gpu');
                if (!data.hasGpu) {
                    gpuBox.disabled = true;
                    gpuBox.checked = false;
                    gpuLabel.innerText = "GPU加速(不可用)";
                    gpuLabel.classList.add('text-rose-500');
                }
            }

            const iconPlay = document.getElementById('icon-play-svg');
            const iconPause = document.getElementById('icon-pause-svg');

            if (state.isRunning && !state.isPaused) {
                iconPlay.classList.add('hidden');
                iconPlay.classList.remove('block');
                iconPause.classList.add('block');
                iconPause.classList.remove('hidden');
                elVideoInfo.innerText = "研判中...";
            } else {
                iconPlay.classList.add('block');
                iconPlay.classList.remove('hidden');
                iconPause.classList.add('hidden');
                iconPause.classList.remove('block');
                if (state.isPaused) {
                    elVideoInfo.innerText = "已暂停";
                }
            }
            
            if (data.playlist && data.playlist.length > 0) {
                elPlaylist.classList.remove('hidden');
                elPlaylist.innerHTML = '';
                data.playlist.forEach((p, idx) => {
                    const fname = p.split(/[/\\]/).pop();
                    let statusClass = 'bg-white/5 text-slate-500'; 
                    let iconId = 'icon-clock';
                    if (idx < data.playlistIndex) {
                        statusClass = 'bg-green-500/10 text-green-400 border border-green-500/20'; 
                        iconId = 'icon-eye'; // 已处理
                    } else if (idx === data.playlistIndex) {
                        statusClass = 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30'; 
                        iconId = 'icon-play'; // 正在处理
                    }
                    
                    const item = document.createElement('div');
                    item.className = `px-2 py-1 rounded-lg inline-flex items-center gap-1.5 ${statusClass} transition-all duration-300 transform hover:scale-105`;
                    item.innerHTML = `
                        <svg class="w-3.5 h-3.5"><use href="#${iconId}"></use></svg>
                        <span class="truncate max-w-[120px] font-medium text-xs">${fname}</span>
                    `;
                    elPlaylist.appendChild(item);
                });
            } else {
                elPlaylist.classList.add('hidden');
            }
        } catch(e) {
            console.log("Waiting for backend...");
        }
    }

    async function fetchEvents() {
        if (!state.isRunning && !state.isPaused && state.lastEventId === -1) return;
        try {
            const resp = await fetch(`/api/events?since=${state.lastEventId}`);
            const data = await resp.json();
            
            if (data.events && data.events.length > 0) {
                if (elEventsEmpty) elEventsEmpty.style.display = 'none';

                data.events.forEach(ev => {
                    // 创建抓拍卡片
                    const card = document.createElement('div');
                    card.className = "flex bg-black/20 border border-white/5 rounded-xl p-3 items-center gap-3 hover:bg-white/5 transition";
                    card.innerHTML = `
                        <div class="w-16 h-16 rounded-md overflow-hidden bg-slate-800 shrink-0 border border-white/10 group-hover:border-cyan-500/50 transition-colors">
                            <img src="${ev.imageBlob}" class="w-full h-full object-cover" />
                        </div>
                        <div class="flex flex-col flex-1 min-w-0">
                            <div class="flex justify-between items-start mb-1">
                                <div class="flex items-center gap-1.5 truncate">
                                    <svg class="w-3 h-3 text-cyan-400 shrink-0"><use href="#icon-target"></use></svg>
                                    <span class="text-xs font-bold text-slate-200 truncate">${ev.label}</span>
                                </div>
                                <span class="text-[10px] text-cyan-400 bg-cyan-400/10 px-1.5 py-0.5 rounded border border-cyan-400/20">${ev.timeStr}</span>
                            </div>
                            <div class="flex items-center gap-1 opacity-60">
                                <svg class="w-2.5 h-2.5"><use href="#icon-box"></use></svg>
                                <span class="text-[10px] text-slate-500">特征匹配完成</span>
                            </div>
                        </div>
                    `;
                    elEventsCont.prepend(card);
                    state.lastEventId = ev.id;
                    state.totalEvents++;
                });

                elEventCount.innerText = state.totalEvents;
            }
        } catch(e) { }
    }
});

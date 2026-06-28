(function() {
    class SoundManager {
        constructor() {
            this.audioContext = null;
            this.clickBuffer = null;
            this.trueBuffer = null;
            this.falseBuffer = null;
            this.backgroundBuffer = null;
            this.backgroundSource = null;
            this.backgroundGain = null;
            this.isInitialized = false;
            this.hasUserInteraction = false;
            this.effectsEnabled = true;
            this.backgroundEnabled = true;
            this.effectsVolume = 0.7;
            this.backgroundVolume = 0.3;
            this.isBackgroundPlaying = false;
            this.backgroundStartTime = 0;
            this.lastSavedPosition = 0;
        }

        async init() {
            if (this.isInitialized) return;

            try {
                this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
            } catch (e) {
                console.warn('Web Audio API not supported');
                return;
            }

            try {
                await this.loadAllBuffers();
                this.setupBackgroundGain();
                this.isInitialized = true;
                console.log('SoundManager initialized');
                
                if (this.canAutoPlay()) {
                    this.startBackgroundMusic();
                }
            } catch (e) {
                console.error('Failed to load audio:', e);
            }
        }

        canAutoPlay() {
            try {
                const lastInteraction = localStorage.getItem('dishu_last_interaction');
                if (lastInteraction) {
                    const interactionAge = Date.now() - parseInt(lastInteraction);
                    if (interactionAge < 300000) {
                        return true;
                    }
                }
            } catch (e) {
                console.warn('Failed to check auto play:', e);
            }
            return false;
        }

        saveInteractionState() {
            try {
                localStorage.setItem('dishu_last_interaction', Date.now().toString());
            } catch (e) {
                console.warn('Failed to save interaction state:', e);
            }
        }

        async loadAllBuffers() {
            const urls = {
                click: '/music/click.mp3',
                true: '/music/true.mp3',
                false: '/music/false.mp3',
                background: '/music/bgMusic.mp3'
            };

            const loadBuffer = async (url) => {
                const response = await fetch(url);
                const arrayBuffer = await response.arrayBuffer();
                return this.audioContext.decodeAudioData(arrayBuffer);
            };

            const promises = [
                loadBuffer(urls.click).then(buf => { this.clickBuffer = buf; }),
                loadBuffer(urls.true).then(buf => { this.trueBuffer = buf; }),
                loadBuffer(urls.false).then(buf => { this.falseBuffer = buf; }),
                loadBuffer(urls.background).then(buf => { this.backgroundBuffer = buf; })
            ];

            await Promise.all(promises);
        }

        setupBackgroundGain() {
            this.backgroundGain = this.audioContext.createGain();
            this.backgroundGain.gain.value = this.backgroundVolume;
            this.backgroundGain.connect(this.audioContext.destination);
        }

        ensureContextRunning() {
            if (this.audioContext && this.audioContext.state === 'suspended') {
                this.audioContext.resume();
            }
        }

        playClick() {
            if (!this.effectsEnabled || !this.clickBuffer) return;
            this.ensureContextRunning();
            this.playBuffer(this.clickBuffer, this.effectsVolume);
        }

        playTrue() {
            if (!this.effectsEnabled || !this.trueBuffer) return;
            this.ensureContextRunning();
            this.playBuffer(this.trueBuffer, this.effectsVolume);
        }

        playFalse() {
            if (!this.effectsEnabled || !this.falseBuffer) return;
            this.ensureContextRunning();
            this.playBuffer(this.falseBuffer, this.effectsVolume);
        }

        playBuffer(buffer, volume) {
            if (!this.audioContext || !buffer) return;
            
            const source = this.audioContext.createBufferSource();
            const gainNode = this.audioContext.createGain();
            
            source.buffer = buffer;
            gainNode.gain.value = volume;
            
            source.connect(gainNode);
            gainNode.connect(this.audioContext.destination);
            
            source.start();
        }

        getCurrentPosition() {
            if (!this.isBackgroundPlaying || !this.backgroundSource || !this.audioContext) {
                return this.lastSavedPosition;
            }
            const elapsed = this.audioContext.currentTime - this.backgroundStartTime;
            return elapsed % this.backgroundBuffer.duration;
        }

        savePosition() {
            try {
                const position = this.getCurrentPosition();
                sessionStorage.setItem('dishu_music_pos', position.toString());
                sessionStorage.setItem('dishu_music_time', Date.now().toString());
                this.lastSavedPosition = position;
                console.log('Saved position:', position.toFixed(2), 'seconds');
            } catch (e) {
                console.warn('Failed to save position:', e);
            }
        }

        loadPosition() {
            try {
                const savedPos = sessionStorage.getItem('dishu_music_pos');
                const savedTime = sessionStorage.getItem('dishu_music_time');
                
                if (savedPos && savedTime) {
                    const pos = parseFloat(savedPos);
                    const elapsed = (Date.now() - parseInt(savedTime)) / 1000;
                    const bufferDuration = this.backgroundBuffer?.duration || 1;
                    let newPos = pos + elapsed;
                    
                    while (newPos >= bufferDuration) {
                        newPos -= bufferDuration;
                    }
                    
                    console.log('Loaded position:', pos.toFixed(2), '+ elapsed:', elapsed.toFixed(2), '=>', newPos.toFixed(2));
                    return newPos;
                }
            } catch (e) {
                console.warn('Failed to load position:', e);
            }
            return 0;
        }

        startBackgroundMusic() {
            if (!this.backgroundEnabled || !this.backgroundBuffer) return;
            
            this.ensureContextRunning();
            
            if (this.backgroundSource) {
                try {
                    this.backgroundSource.stop();
                } catch (e) {
                    console.warn('Error stopping existing source:', e);
                }
            }

            const startPosition = this.loadPosition();

            this.backgroundSource = this.audioContext.createBufferSource();
            this.backgroundSource.buffer = this.backgroundBuffer;
            this.backgroundSource.loop = true;
            
            this.backgroundSource.connect(this.backgroundGain);
            
            this.backgroundStartTime = this.audioContext.currentTime - startPosition;
            this.backgroundSource.start(0, startPosition);
            this.isBackgroundPlaying = true;
            this.lastSavedPosition = startPosition;
            this.saveInteractionState();
            
            console.log('Background music started from position:', startPosition.toFixed(2), 'seconds');
            
            if (this.positionInterval) {
                clearInterval(this.positionInterval);
            }
            this.positionInterval = setInterval(() => {
                if (this.isBackgroundPlaying) {
                    this.savePosition();
                }
            }, 500);
        }

        stopBackgroundMusic() {
            if (this.positionInterval) {
                clearInterval(this.positionInterval);
            }
            if (this.backgroundSource) {
                try {
                    this.backgroundSource.stop();
                } catch (e) {
                    console.warn('Error stopping background music:', e);
                }
                this.backgroundSource = null;
            }
            this.isBackgroundPlaying = false;
            this.savePosition();
            console.log('Background music stopped');
        }

        handleFirstInteraction() {
            if (!this.hasUserInteraction) {
                this.hasUserInteraction = true;
                this.saveInteractionState();
                this.startBackgroundMusic();
                console.log('First user interaction detected');
            } else {
                this.saveInteractionState();
            }
        }

        setBackgroundVolume(volume) {
            this.backgroundVolume = Math.max(0, Math.min(1, volume));
            if (this.backgroundGain) {
                this.backgroundGain.gain.value = this.backgroundVolume;
            }
        }

        setEffectsVolume(volume) {
            this.effectsVolume = Math.max(0, Math.min(1, volume));
        }
    }

    const soundManager = new SoundManager();

    document.addEventListener('DOMContentLoaded', () => {
        soundManager.init();

        const handleFirstInteraction = () => {
            soundManager.handleFirstInteraction();
            document.removeEventListener('click', handleFirstInteraction);
            document.removeEventListener('keydown', handleFirstInteraction);
            document.removeEventListener('touchstart', handleFirstInteraction);
        };

        document.addEventListener('click', handleFirstInteraction, { once: true });
        document.addEventListener('keydown', handleFirstInteraction, { once: true });
        document.addEventListener('touchstart', handleFirstInteraction, { once: true });

        document.addEventListener('click', (e) => {
            const target = e.target;
            if (target.tagName === 'BUTTON' || 
                target.closest('button') || 
                target.tagName === 'A' ||
                target.closest('a')) {
                soundManager.playClick();
            }
        });

        window.addEventListener('beforeunload', () => {
            soundManager.savePosition();
        });
    });

    window.soundManager = soundManager;
})();

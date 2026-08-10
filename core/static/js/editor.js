import { formatSecondsToString, createElementFromHTMLString, getCSRFToken, animateDuringPlayback, applyRect } from "./utils.js";
import { BlurEditor, placeLocators } from "./BlurEditor.js";
import {
  RESIZE_HANDLES,
  clampRect,
  edgesForHandle,
  percentWithin,
  pointsLostByRetiming,
  resizeRect,
} from "./video-geometry.js";

const SEEK_REGION_SELECTOR = '#timeline-row-ticks-and-scrubbers, .timeline-track-row-right';
const COMMENT_MIN_WIDTH = 3;
const COMMENT_MIN_HEIGHT = 4;
const MIN_ITEM_SECONDS = 0.1;

function convertPercentStringToDecimal(percentString) {
  if (typeof(percentString) === 'string') {
    const newString = percentString.replace('%', '');
    return parseFloat(newString) / 100;
  }

  return;
}

export class Editor {
    constructor() {
        this.video = document.querySelector('#video-player');
        this.duration = this.video.duration;
        this.dragState = null;
        this.contentId = null;
        this.listenForNewItemCreation();
        this.typeOfAnnotationInFocus = null;
        this.annotationIdInFocus = null;
        this.tickMarksContainer = document.querySelector('#tick-marks-container');
        this.timelineTicksContent = document.getElementById('timeline-ticks-content');
        this.timelineWrapper = document.getElementById('timeline-wrapper');
        this.zoomSliderInput = document.getElementById('timeline-scroll-input');
        this.editorScrubber = document.querySelector('#editor-scrubber');
        this.zoomLevel = 1;
        this.timelineScrollRatio = 0;
        this.scrubberBounds = null;
        this.timelineScrubber = null;
        this.isDragging = false;
        this.wasPlayingBeforeDrag = false;
        this.annotationUpdatedEvent = new CustomEvent("annotationUpdated");
        this.lastSavedItemFormState = null;
        this.historyRequestInFlight = false;
        this.saveItemForm = null;
        this.selectedSubtitleTrackId = null;
        this.itemBeingDragged = null;
        this.dragGrabOffsetX = 0;
        this.activeTrackId = null;
        this.dragGhostImage = new Image();  // Used to avoid browser's default globe icon
        this.dragGhostImage.src = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";

        // The comment editor's own overlay - see _ensureCommentRig for why it is not the player's box.
        this.commentRig = null;
        this.commentRigAnnotationId = null;
        this.commentRigWindow = null;
        // Set while a pointer is down, so the render below cannot overwrite the geometry the user is
        // actively dragging.
        this.commentRigDragging = false;
        this.renderCommentRig = this.renderCommentRig.bind(this);
        // The same two cadences BlurEditor uses: timeupdate/seeked cover scrubbing and the paused
        // case, while animateDuringPlayback runs every animation frame, the rate at which the player
        // adds and removes the comment box the rig is drawn around.
        this.video.addEventListener("timeupdate", this.renderCommentRig);
        this.video.addEventListener("seeked", this.renderCommentRig);
        animateDuringPlayback(this.video, this.renderCommentRig);

        this.annotationBox = window.videoPlayer.annotationBox;

        this.blurEditor = new BlurEditor({
          video: this.video,
          player: window.videoPlayer,
          timelineWrapper: this.timelineWrapper,
          onPositionsSaved: () => {
            this.placeTrackItems();
            this.setupItems();
          },
        });

        this.init();
    }

    init() {
        const playerContainer = document.getElementById("annotation-player-container");
        this.contentId = playerContainer.dataset["contentid"];
        // Event delegation for drag/resize - selection is handled by HTMX attributes
        this.updateTracks();
        document.addEventListener('mousemove', this.handleMouseMove.bind(this));
        document.addEventListener('mouseup', this.handleMouseUp.bind(this));

        this.placeTrackItems();
        document.body.addEventListener('htmx:afterSettle', this.handleTrackItemPlacementAfterEvent.bind(this));

        this.renderTickMarksAndLabels();
        this.attachZoomListener();
        this.createtimelineScrubber();
        this.attachTimelineListeners();
        this.attachVideoListeners();
        this.setupTracks();
        this.watchForTrackCreation();
        this.watchForClickOutsideOfTrackMenu();
        this.watchForTimelineScrollChangeAndHandleIt();
        this.watchAndHandleAnnotationSetMenuOpen();
        this.watchForAnnotationSetNameChangeAndHandleIt();
        this.watchAndHandleAnnotationSetDelete();
        this.setupAnnotationSetOptionsModal();
        this.listenForHistoryControls();
        this.watchAndHandleAnnotationSetExport();
        this.attachRemoveEditorListeners();
        this.watchForEditorSearchInputAndHandleIt();
        this.watchAndHandleEditorPanelSwitch();
        this.watchAndHandleSubtitleTrackChange();
        this.handleNoAnnotationSet();
    }

    updateTracks() {
      const tracks = document.querySelectorAll('.track-row-annotations-container');
      tracks.forEach(container => {
          container.addEventListener('mousedown', this.handleMouseDown.bind(this));
      });
      this.tracks = tracks;
      this.setActiveTrack(this.activeTrackId);  // Re-apply active track highlight after tracks are rebuilt
    }

    setActiveTrack(trackId) {
      const trackRows = document.getElementsByClassName("track-row");
      const activeTrackExists = Array.from(trackRows).some(row => row.dataset["trackId"] == trackId);
      this.activeTrackId = activeTrackExists ? trackId : trackRows[0]?.dataset["trackId"];
      for (let trackRow of trackRows) {
        trackRow.classList.toggle("active-track", trackRow.dataset["trackId"] == this.activeTrackId);
      }
    }

    startResize(trackItem, handle, e) {
        const isLeft = handle.classList.contains('resize-handle-left');
        const itemContainer = trackItem.closest('.track-row-annotations-container');
        const containerWidth = itemContainer.getBoundingClientRect().width;
        const itemLeft = this.calculateItemLeftAsDecimal(trackItem);
        const itemWidth = this.calculateItemWidthAsDecimal(trackItem);

        this.dragState = {
            type: 'resize',
            item: trackItem,
            container: itemContainer,
            handle,
            isLeft,
            startX: e.clientX,
            startLeft: itemLeft * 100,
            startWidth: itemWidth * 100,
            containerWidth,
            hasMoved: false,
            originalLeft: itemLeft,
            originalWidth: itemWidth
        };

        // Reset deltas
        trackItem.dataset.deltaLeft = '0';
        trackItem.dataset.deltaWidth = '0';

        document.body.classList.add('resizing', 'resizing-item');

        // Hide the hover scrubber for the duration of the resize
        if (this.timelineScrubber) this.timelineScrubber.style.opacity = '0';

        // Seek video to the handle position being dragged
        this.seekToHandlePosition(isLeft, parseFloat(trackItem.style.left), parseFloat(trackItem.style.width));
    }

    handleMouseDown(e) {
        const trackItem = e.target.closest('.track-item');
        if (!trackItem) return;

        const resizeHandle = e.target.closest('.resize-handle');

        if (resizeHandle) {
            this.startResize(trackItem, resizeHandle, e);
            e.preventDefault();
            e.stopPropagation();
        }
    }

    calculateItemLeftAsDecimal(item) {
      const startTime = parseFloat(item.dataset["start"]);
      return startTime / this.duration;

    }

    calculateItemWidthAsDecimal(item) {
      const startTime = parseFloat(item.dataset["start"]);
      const endTime = parseFloat(item.dataset["end"]);
      return (endTime - startTime) / this.duration;
    }

    hideOrShowResizeHandles(itemElement) {
      // we have to explicitly check for "track-item" class because other class names
      // contain the string "track-item". Otherwise we could have just checked for the
      // "track-item" substring in element.className
      let isTrackItem = false;
      for (let cl of itemElement.classList) {
        if (cl == "track-item") {
          isTrackItem = true;
          break;
        }
      }

      if (!isTrackItem) return;

      const narrowClass = "resize-handle-narrow";
      const resizeHandles = itemElement.querySelectorAll(".resize-handle");
      // getBoundingClientRect provides size values in px.
      const triggerWidth = 60;
      const shouldNarrowHandles = itemElement.getBoundingClientRect().width < triggerWidth;
      for (let handle of resizeHandles) {
        if (shouldNarrowHandles) {
          handle.classList.add(narrowClass);
        }
        else {
          handle.classList.remove(narrowClass);
        }
      }
    }

    handleMouseMove(e) {
        if (!this.dragState) return;

        // Define a threshold (in pixels) to determine if this is a real drag
        const DRAG_THRESHOLD = 3;

        const deltaX = Math.abs(e.clientX - this.dragState.startX);

        // Only mark as moved if we've exceeded the threshold
        if (deltaX > DRAG_THRESHOLD) {
            this.dragState.hasMoved = true;
        }

        // Only update position if we've started moving
        if (this.dragState.hasMoved) {
            if (this.dragState.type === 'resize') {
                this.updateResizePosition(e);
                this.hideOrShowResizeHandles(this.dragState.item);
            }
        }

        e.preventDefault();
    }

    updateResizePosition(e) {
        const deltaX = e.clientX - this.dragState.startX;
        // Don't account for zoom in percent calculation - container width is already adjusted
        const deltaPercent = (deltaX / this.dragState.containerWidth) * 100;
        const minWidthPercent = (MIN_ITEM_SECONDS / this.duration) * 100;

        if (this.dragState.isLeft) {
            let newLeft = this.dragState.startLeft + deltaPercent;
            let newWidth = this.dragState.startWidth - deltaPercent;

            newLeft = Math.max(0, newLeft);
            newWidth = Math.max(minWidthPercent, newWidth);

            if (newLeft + newWidth > 100) {
                newWidth = 100 - newLeft;
            }

            this.dragState.item.style.left = `${newLeft}%`;
            this.dragState.item.style.width = `${newWidth}%`;

            const deltaLeft = newLeft - this.dragState.originalLeft;
            const deltaWidth = newWidth - this.dragState.originalWidth;
            this.dragState.item.dataset.deltaLeft = deltaLeft.toFixed(2);
            this.dragState.item.dataset.deltaWidth = deltaWidth.toFixed(2);

            this.seekToHandlePosition(true, newLeft, newWidth);
        } else {
            let newWidth = this.dragState.startWidth + deltaPercent;

            newWidth = Math.max(minWidthPercent, newWidth);
            const maxWidth = 100 - this.dragState.startLeft;
            newWidth = Math.min(newWidth, maxWidth);

            this.dragState.item.style.width = `${newWidth}%`;

            const deltaWidth = newWidth - this.dragState.originalWidth;
            this.dragState.item.dataset.deltaWidth = deltaWidth.toFixed(2);

            this.seekToHandlePosition(false, this.dragState.startLeft, newWidth);
        }

        this.updateBlurLocatorsDuringResize();
    }

    updateBlurLocatorsDuringResize() {
        const item = this.dragState?.item;
        if (!item || item.dataset.annotationType !== "blur") return;

        const leftPercent = parseFloat(item.style.left);
        const widthPercent = parseFloat(item.style.width);
        if (!Number.isFinite(leftPercent) || !Number.isFinite(widthPercent)) return;

        placeLocators(item, {
            start: (leftPercent / 100) * this.duration,
            end: ((leftPercent + widthPercent) / 100) * this.duration,
        });
    }

    seekToHandlePosition(isLeft, leftPercent, widthPercent) {
        const video = document.querySelector('#video-player');
        if (!video) return;

        // Calculate time based on which handle is being dragged
        const timePercent = isLeft ? leftPercent : (leftPercent + widthPercent);
        const targetTime = (timePercent / 100) * this.duration;

        // Seek the video (this will trigger timeupdate and update the scrubber)
        video.currentTime = targetTime;

        // Also update via the player API if available
        if (window.videoPlayer && window.videoPlayer.skipTo) {
            window.videoPlayer.skipTo(targetTime);
        }
    }

    handleMouseUp(e) {
        if (!this.dragState) return;

        const state = this.dragState;
        this.dragState = null;

        document.body.classList.remove('resizing', 'resizing-item');

        if (state.hasMoved) {
            this.triggerSave(state);
            // Prevent the click event from firing if we actually dragged
            e.preventDefault();
            e.stopPropagation();

            // Also prevent the next click event (for better browser compatibility)
            const preventNextClick = (clickEvent) => {
                clickEvent.preventDefault();
                clickEvent.stopPropagation();
                document.removeEventListener('click', preventNextClick, true);
            };
            document.addEventListener('click', preventNextClick, true);
        }
        // If !hasMoved, don't prevent - let the click bubble to HTMX
    }

    setUpItemElevationOnMousedown(item) {
      item.addEventListener("mousedown", () => {
        const parent = item.closest(".track-row-annotations-container");
        for (let sibling of parent.querySelectorAll(".track-item")) {
          if (sibling != item) {
            sibling.classList.remove("elevated");
          } else {
            sibling.classList.add("elevated");
          }
        }
      });
    }

    setUpItemClickListeners(element) {
      if (element.dataset.clickSetup === "true") {
        return;
      }
      const annotationType = element.dataset["annotationType"];
      const annotationId = element.dataset["annotationId"];
      element.addEventListener("click", async (e) => {
        e.preventDefault();
        this.getItemFormDetails(annotationType, annotationId, this.contentId);
        this.markItemAsActive(annotationType, annotationId);
      });
      element.dataset.clickSetup = "true";
    }

    blockTrackItemPointerEvents() {
      const containers = document.getElementsByClassName("track-row-annotations-container");
      for (let container of containers) {
        container.classList.add("annotations-container-no-child-pointer-events");
      }
    }

    allowTrackItemPointerEvents() {
      const containers = document.getElementsByClassName("track-row-annotations-container");
      for (let container of containers) {
        container.classList.remove("annotations-container-no-child-pointer-events");
      }
    }

    resetTrackItemProjectionsStyle() {
      const projections = this.timelineWrapper.querySelectorAll(".track-item-projection");
      for (let projection of projections) {
        projection.style.width = "";
        projection.style.left = "";
        projection.style.top = "5px";
      }
    }

    setupItemDragListeners(item) {
      // setup the data stored in the drag event
      item.addEventListener("dragstart", (event) => {
        this.itemBeingDragged = item;
        this.dragGrabOffsetX = event.clientX - item.getBoundingClientRect().left;
        this.resetTrackItemProjectionsStyle();
        this.blockTrackItemPointerEvents();
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/html", item.outerHTML);
        event.dataTransfer.setData("text/plain", item.id);
        event.dataTransfer.setDragImage(this.dragGhostImage, 0, 0);
        item.classList.add("is-dragging");
      });

      item.addEventListener("dragend", () => {
        this.itemBeingDragged = null;
        item.classList.remove("is-dragging");
        this.allowTrackItemPointerEvents();
      })
    }

    setupItems() {
      const itemsToSetUp = document.querySelectorAll(".track-item[data-setup='false']");
      for (let item of itemsToSetUp) {
        this.setUpItemElevationOnMousedown(item);
        this.setUpItemClickListeners(item);
        this.setupItemDragListeners(item);
        item.dataset["setup"] = "true";
      }
    }

    serializeItemForm(itemForm) {
      return new URLSearchParams(new FormData(itemForm)).toString();
    }

    autoSaveItemForm() {
      const itemForm = document.getElementById("annotation-update-form");
      if (!itemForm) {
        this.saveItemForm = null;
        return;
      }
      this.lastSavedItemFormState = this.serializeItemForm(itemForm);

      let saveInFlight = false;
      let saveQueued = false;

      const save = this.saveItemForm = async () => {
        if (saveInFlight) {
          saveQueued = true;
          return;
        }
        saveInFlight = true;
        try {
          const state = this.serializeItemForm(itemForm);
          if (state === this.lastSavedItemFormState) return;

          const annotationId = itemForm.dataset["annotationId"];
          const annotationType = itemForm.dataset["annotationType"];

          if (annotationType == "blur") {
            const item = this.timelineWrapper.querySelector(`.track-item[data-annotation-type="blur"][data-annotation-id="${annotationId}"]`);
            const newStart = parseFloat(itemForm.querySelector("#start_time")?.value);
            const newEnd = parseFloat(itemForm.querySelector("#end_time")?.value);
            if (item && Number.isFinite(newStart) && Number.isFinite(newEnd) &&
                !this.confirmBlurPointLoss(item, newStart, newEnd)) {
              itemForm.querySelector("#start_time").value = item.dataset["start"];
              itemForm.querySelector("#end_time").value = item.dataset["end"];
              return;
            }
          }

          this.lastSavedItemFormState = state;
          const updated = await this.updateAnnotation({annotationType, annotationId, autoUpdateForm: false});
          if (!updated) {
            this.lastSavedItemFormState = null;
            return;
          }
          this.refreshItemFormFromServerState(itemForm, updated);
        } finally {
          saveInFlight = false;
          if (saveQueued) {
            saveQueued = false;
            save();
          }
        }
      };

      itemForm.addEventListener("change", (e) => {
        if (!e.target.name) return;
        save();
      });

      itemForm.addEventListener("submit", (e) => {
        e.preventDefault();
        if (e.submitter?.classList.contains("blur-position-delete-button")) {
          return;
        }
        save();
      });
    }

    refreshItemFormFromServerState(itemForm, responseData) {
      const annotationType = responseData["annotation_type"];
      const annotationId = responseData["annotation_id"];
      const item = document.getElementById(`${annotationType}-${annotationId}`);
      for (const [fieldId, datasetKey] of [["start_time", "start"], ["end_time", "end"]]) {
        const input = itemForm.querySelector(`#${fieldId}`);
        const stored = item?.dataset[datasetKey];
        if (input && stored !== undefined && input !== document.activeElement) {
          input.value = stored;
        }
      }

      if (annotationType != "blur") return;
      const incoming = createElementFromHTMLString(responseData["form_html"]);
      const rows = incoming?.querySelector("#positions-list");
      const list = document.getElementById("positions-list");
      if (rows && list) list.replaceWith(rows);
      this.blurEditor.syncFromPanel();
    }

    async deleteItem(annotationType, annotationId) {
      const response = await fetch(`/annotations/${annotationType}/${annotationId}/delete`, {
        method: "delete",
        headers: {"X-CSRFToken": getCSRFToken()}
      });

      if (!response.ok) {
        console.error("Failed to delete item!");
        return;
      }

      if(this.typeOfAnnotationInFocus == "blur") {
        this.blurEditor.deselect();
      }
      window.dispatchEvent(this.annotationUpdatedEvent);

      // remove item from panel
      const panelItemToRemove = document.getElementById(`${annotationType}-panel-item-${annotationId}`);
      if (panelItemToRemove) {
        panelItemToRemove.remove();
      } else {
        console.error("Failed to remove deleted panel item");
      }

      // remove item from track
      const trackItemToRemove = this.timelineWrapper.querySelector(`.track-item[data-annotation-type="${annotationType}"][data-annotation-id="${annotationId}"]`);
      if (trackItemToRemove) {
        trackItemToRemove.remove();
      } else {
        console.error("Failed to remove deleted track item");
      }

      // check if right side panel form should be emptied
      const itemForm = document.getElementById("existing-item-form");
      if (itemForm) {
        const itemFormType = itemForm.dataset["annotationType"];
        const itemFormId = itemForm.dataset["annotationId"];
        if (annotationType == itemFormType && annotationId == itemFormId) {
          const detailForm = document.getElementById("detail-form");
          detailForm.innerHTML = "";
        }
      }
      this.placeTrackItems();
    }

    async setUpItemFormDeleteButton() {
      const itemForm = document.getElementById("existing-item-form");
      if (!itemForm) {
        return;
      }
      const deleteItemButton = itemForm.querySelector("#annotation-form-delete-button");
      if (!deleteItemButton) {
        return;
      }

      deleteItemButton.addEventListener("click", async (e) => {
        e.preventDefault();
        const annotationType = itemForm.dataset["annotationType"];
        const annotationId = itemForm.dataset["annotationId"];

        if (!window.confirm(`Delete this ${annotationType} annotation? This cannot be undone.`)) {
          return;
        }
        await this.deleteItem(annotationType, annotationId);
      });
    }

    setUpItemForm() {
      this.autoSaveItemForm();
      this.setUpItemFormDeleteButton();
      this.changeAnnotationInFocus();
    }

    buildMoveHandler(elementToMove) {
      let lastEvent;
      return (event) => {
        const referenceRect = elementToMove.parentElement.getBoundingClientRect();
        if (lastEvent && referenceRect.width > 0 && referenceRect.height > 0) {
          // Clamped, so a box cannot be dragged off the frame and out of reach.
          const moved = clampRect({
            x: (parseFloat(elementToMove.style.left) || 0) + (event.clientX - lastEvent.x) / referenceRect.width * 100,
            y: (parseFloat(elementToMove.style.top) || 0) + (event.clientY - lastEvent.y) / referenceRect.height * 100,
            width: parseFloat(elementToMove.style.width) || 0,
            height: parseFloat(elementToMove.style.height) || 0,
          });
          elementToMove.style.left = `${moved.x}%`;
          elementToMove.style.top = `${moved.y}%`;
        }
        lastEvent = {x: event.clientX, y: event.clientY};
      }
    }

    buildResizePointMoveHandler(minHeight = COMMENT_MIN_HEIGHT, minWidth = COMMENT_MIN_WIDTH) {
      return (event) => {
        event.stopPropagation();
        const elementToResize = event.target.parentElement;
        const limits = {minWidth, minHeight};
        const pointer = percentWithin(
          {x: event.clientX, y: event.clientY, width: 0, height: 0},
          event.target.closest(".annotation-box").getBoundingClientRect(),
        );
        const origin = {
          x: parseFloat(elementToResize.style.left) || 0,
          y: parseFloat(elementToResize.style.top) || 0,
          width: parseFloat(elementToResize.style.width) || 0,
          height: parseFloat(elementToResize.style.height) || 0,
        };
        const edges = edgesForHandle(event.target.dataset["handle"]);
        if (!edges) return;
        const resized = clampRect(resizeRect(origin, pointer, {
          ...edges,
          ...limits,
        }), limits);
        elementToResize.style.left = `${resized.x}%`;
        elementToResize.style.top = `${resized.y}%`;
        elementToResize.style.width = `${resized.width}%`;
        elementToResize.style.height = `${resized.height}%`;
      }
    }

    changeAnnotationInFocus() {
      const previousTypeInFocus = this.typeOfAnnotationInFocus;
      const itemForm = document.getElementById("existing-item-form");
      if (itemForm == null) {
        this.typeOfAnnotationInFocus = null;
        this.blurEditor.deselect();
        this.removeCommentRig();
        return;
      }

      this.typeOfAnnotationInFocus = itemForm.dataset["annotationType"];
      this.annotationIdInFocus = itemForm.dataset["annotationId"];

      if (previousTypeInFocus == "blur") {
        this.blurEditor.deselect();
      }

      if (previousTypeInFocus == "comment" && this.typeOfAnnotationInFocus != "comment") {
        this.removeCommentRig();
      }

      if (this.typeOfAnnotationInFocus == "blur" ) {
        this.blurEditor.select(this.annotationIdInFocus);
      }
    }

    listenForHistoryControls() {
      document.addEventListener("click", (event) => {
        const button = event.target.closest(".annotation-history-button");
        if (!button || button.disabled) {
          return;
        }
        event.preventDefault();
        this.changeAnnotationVersion(button);
      });

      document.addEventListener("keydown", (event) => {
        const target = event.target;
        const isTextEntry = target.matches?.("input, textarea, select, [contenteditable='true']");
        if (isTextEntry || !(event.ctrlKey || event.metaKey) || event.key?.toLowerCase() !== "z") {
          return;
        }

        // Scoped to the open form rather than the document: the shortcut has to act on the
        // annotation the user is looking at, not on whichever toolbar happens to be first.
        const action = event.shiftKey ? "redo" : "undo";
        const button = document.querySelector(
          `#existing-item-form .annotation-history-button[data-history-action="${action}"]:not(:disabled)`
        );
        if (!button) {
          return;
        }
        event.preventDefault();
        this.changeAnnotationVersion(button);
      });
    }

    async changeAnnotationVersion(button) {
      if (this.historyRequestInFlight) {
        return false;
      }
      this.historyRequestInFlight = true;
      button.disabled = true;

      try {
        const response = await fetch(button.dataset.historyUrl, {
          method: "POST",
          headers: {
            "X-CSRFToken": getCSRFToken(),
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: new URLSearchParams({
            annotation_id: button.dataset.annotationId,
            annotation_type: button.dataset.annotationType,
          }),
        });

        if (!response.ok) {
          console.error(`Failed to ${button.dataset.historyAction} annotation history`);
          if (button.isConnected) {
            button.disabled = false;
          }
          return false;
        }

        const responseData = await response.json();
        const applied = this.applyAnnotationVersionResponse(
          responseData,
          button.dataset.annotationId,
          true,
        );
        window.dispatchEvent(this.annotationUpdatedEvent);
        if (!applied && button.isConnected) {
          // The version did change on the server, but the timeline could not be updated to show
          // it. Leaving the button disabled would strand the user in a state they cannot undo
          // their way out of.
          button.disabled = false;
        }
        return applied;
      } catch (error) {
        console.error(`Failed to ${button.dataset.historyAction} annotation history`, error);
        if (button.isConnected) {
          button.disabled = false;
        }
        return false;
      } finally {
        this.historyRequestInFlight = false;
      }
    }

    replaceAnnotationVersionElements(responseData, previousAnnotationId) {
      const annotationType = responseData.annotation_type;
      const annotationId = String(responseData.annotation_id);
      const trackId = String(responseData.track_id);
      const previousItem = document.getElementById(`${annotationType}-${previousAnnotationId}`);
      const destination = document.querySelector(
        `.track-row[data-track-id="${trackId}"] .track-row-annotations-container`
      );

      if (!destination) {
        console.error("Could not place the active annotation version in the timeline");
        return false;
      }

      // A missing previous item is not fatal: the new version still belongs on the timeline, and
      // dropping it would leave the annotation invisible even though the save succeeded.
      const newItem = createElementFromHTMLString(responseData.item_html);
      if (previousItem?.parentElement === destination) {
        previousItem.replaceWith(newItem);
      } else {
        previousItem?.remove();
        destination.appendChild(newItem);
      }

      const previousPanelItem = document.getElementById(
        `${annotationType}-panel-item-${previousAnnotationId}`
      );
      if (previousPanelItem) {
        previousPanelItem.replaceWith(
          createElementFromHTMLString(responseData.panel_item_html)
        );
      }

      this.placeTrackItems();
      return annotationId;
    }

    syncOpenFormToVersion(responseData, previousAnnotationId) {
      const itemForm = document.getElementById("existing-item-form");
      const updateForm = document.getElementById("annotation-update-form");
      if (!itemForm || !updateForm) {
        return false;
      }

      if (String(itemForm.dataset.annotationId) !== String(previousAnnotationId)) {
        return false;
      }

      const annotationType = responseData.annotation_type;
      const annotationId = String(responseData.annotation_id);
      // Asked of the rig rather than of the player's box, which is why this no longer has to wait:
      // the rig is this module's own element and outlives the save, so whether controls were on
      // screen is a fact already in hand instead of one the player's next reload decides.
      const hadCommentRig =
        annotationType == "comment" &&
        this.commentRigAnnotationId === String(previousAnnotationId);

      itemForm.dataset.annotationId = annotationId;
      updateForm.dataset.annotationId = annotationId;
      this.annotationIdInFocus = annotationId;

      const renderedForm = createElementFromHTMLString(responseData.form_html);
      const renderedToolbar = renderedForm.querySelector(".undo-redo-toolbar");
      const currentToolbar = itemForm.querySelector(".undo-redo-toolbar");
      if (renderedToolbar && currentToolbar) {
        currentToolbar.replaceWith(renderedToolbar);
      }

      if (annotationType == "blur") {
        this.blurEditor.retarget(annotationId);
      }
      if (hadCommentRig) {
        this.presentCommentBoxPositionAndSizeControls(annotationId);
      }
      return true;
    }

    applyAnnotationVersionResponse(responseData, previousAnnotationId, replaceForm) {
      const placed = this.replaceAnnotationVersionElements(
        responseData,
        previousAnnotationId,
      );

      const annotationType = responseData.annotation_type;
      const annotationId = String(responseData.annotation_id);
      const detailForm = document.getElementById("detail-form");
      if (replaceForm) {
        detailForm.innerHTML = responseData.form_html;
        this.setUpLoadedItemForm(annotationType, annotationId);
      } else {
        this.syncOpenFormToVersion(responseData, previousAnnotationId);
      }

      if (!placed) {
        return false;
      }
      this.markItemAsActive(annotationType, annotationId, {navigate: replaceForm});
      return true;
    }

    async updateAnnotation({annotationType, annotationId, name=undefined, description=undefined, startTime=undefined, endTime=undefined, trackId=undefined, isFromItem=false, autoUpdateForm=true}) {

      let requestBody, contentType;
      if (isFromItem) {
        requestBody = JSON.stringify({
          "content_id": this.contentId,
          "annotation_name": name,
          "description": description,
          "start_time": startTime,
          "end_time": endTime,
          "track_id": trackId,
        });
        contentType = "application/json";
      } else {
        const annotationUpdateForm = document.getElementById("annotation-update-form");
        const formData = new FormData(annotationUpdateForm);
        requestBody = {};
        for (let pair of formData.entries()) {
          const key = pair[0];
          const value = pair[1];
          requestBody[key] = value;
        }
        requestBody = JSON.stringify(requestBody);
        contentType = "application/json";
      }

      const response = await fetch(`/annotations/${annotationType}/${annotationId}/update/`, {
        method: "POST",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": contentType,
        },
        body: requestBody
      });

      if (!response.ok) {
        console.error("An error occurred while updating an annotation");
        return false;
      }

      const responseData = await response.json();
      this.applyAnnotationVersionResponse(responseData, annotationId, autoUpdateForm);
      window.dispatchEvent(this.annotationUpdatedEvent);
      return responseData;
    }

    triggerSave(state) {
        const item = state.item;
        const annotationType = item.dataset["annotationType"];
        const annotationId = item.dataset["annotationId"];

        const stateTypeIsUnknown = state.type !== "resize" && state.type !== "drag";
        const startAndEndTimesAreUnknown = !item.style.left || item.style.left == '' || !item.style.width || item.style.width == '';

        if (stateTypeIsUnknown) {
          console.error("Unknown state type:", state.type);
        }

        if (startAndEndTimesAreUnknown) {
          console.error("Could not determine item start and end times");
        }

        if (stateTypeIsUnknown || startAndEndTimesAreUnknown) {
          // Revert on error
          item.style.left = `${state.originalLeft}%`;
          item.style.width = `${state.originalWidth}%`;
          item.dataset.deltaLeft = '0';
          item.dataset.deltaWidth = '0';
          return;
        }

        const leftAsDecimal = convertPercentStringToDecimal(item.style.left)
        if (isNaN(leftAsDecimal)) {
          console.error(`Unable to parse item's left position: ${item.style.left}`);
          return;
        }
        const newStartTime = leftAsDecimal * this.duration;
        const widthAsDecimal = convertPercentStringToDecimal(item.style.width)
        if (isNaN(widthAsDecimal)) {
          console.error(`Unable to parse item's width: ${item.style.width}`);
          return;
        }
        const newEndTime = (leftAsDecimal + widthAsDecimal) * this.duration;

        if (!this.confirmBlurPointLoss(item, newStartTime, newEndTime)) {
          item.dataset.deltaLeft = '0';
          item.dataset.deltaWidth = '0';
          this.placeTrackItems();
          return;
        }

        this.updateAnnotation({annotationType, annotationId, startTime: newStartTime, endTime: newEndTime, isFromItem: true});
    }

    blurPointsLostByRetiming(item, newStart, newEnd) {
      if (item.dataset["annotationType"] !== "blur") return 0;
      const oldStart = parseFloat(item.dataset["start"]);
      const oldEnd = parseFloat(item.dataset["end"]);
      const dots = item.querySelectorAll(".blur-position-locator");
      const times = [oldStart, ...Array.from(dots, (dot) => parseFloat(dot.dataset["positionTime"]))];
      return pointsLostByRetiming(times, oldStart, oldEnd, newStart, newEnd);
    }

    confirmBlurPointLoss(item, newStart, newEnd) {
      const lost = this.blurPointsLostByRetiming(item, newStart, newEnd);
      if (lost === 0) return true;
      const subject = lost === 1 ? "1 blur point falls" : `${lost} blur points fall`;
      return window.confirm(`${subject} outside the new time range and will be removed. Continue?`);
    }

    setUpCommentChangeListeners(formElement) {
      const itemForm = formElement.querySelector("#existing-item-form");
      const fontSizeInput = formElement.querySelector("#font-size");
      function getCommentBoxOrWriteError() {
        const annotationId = itemForm.dataset["annotationId"];
        const commentTextBox = document.getElementById("comment-text-box-" + annotationId);
        if (!commentTextBox) {
          console.error("could not find comment text box with annotation id: " + annotationId);
          return undefined;
        }
        return commentTextBox;
      }
      let updateTimerId;
      const update = () => {
        clearTimeout(updateTimerId);
        updateTimerId = setTimeout(() => {
          if (!itemForm.isConnected) {
            return;
          }
          this.saveItemForm?.();
        }, 250);
      }

      // handle font size change
      fontSizeInput.addEventListener("input", () => {
        const commentTextBox = getCommentBoxOrWriteError();
        if (!commentTextBox) return;

        const newFontSize = fontSizeInput.value;
        if (newFontSize != undefined && newFontSize != '') {
          commentTextBox.style.fontSize = newFontSize + 'rem';
          update();
        }
      });

      // handle font color change
      const fontColorInput = itemForm.querySelector("#font-color");
      fontColorInput.addEventListener("input", () => {
        const commentTextBox = getCommentBoxOrWriteError();
        if (!commentTextBox) return;

        const newFontColor = fontColorInput.value;
        const newLength = newFontColor.length;
        if (newLength != 3 && newLength != 6) {
          return;
        }
        else {
          commentTextBox.style.color = "#" + fontColorInput.value;
          update();
        }
      });

      const repaintFromForm = () => {
        this.renderCommentRig();
        this._previewCommentBox();
        update();
      };
      for (const selector of ["#top-x", "#top-y", "#bottom-x", "#bottom-y"]) {
        itemForm.querySelector(selector)?.addEventListener("input", repaintFromForm);
      }
    }

    removeCommentRig() {
      this.commentRig?.remove();
      this.commentRig = null;
      this.commentRigAnnotationId = null;
      this.commentRigWindow = null;
      this.commentRigDragging = false;
    }

    updateCommentBoxPositionAndSize() {
      const updateForm = document.getElementById("annotation-update-form");
      const rig = this.commentRig;
      if (!rig || rig.hidden || !updateForm) return;

      const annotationId = updateForm.dataset["annotationId"];
      if (this.commentRigAnnotationId !== String(annotationId)) return;

      // update the form and save
      const boxTop = parseFloat(rig.style.top);
      const boxLeft = parseFloat(rig.style.left);

      const formTopX = updateForm.querySelector("#top-x");
      const formTopY = updateForm.querySelector("#top-y");
      const formBottomX = updateForm.querySelector("#bottom-x");
      const formBottomY = updateForm.querySelector("#bottom-y");
      if (!formTopX || !formTopY || !formBottomX || !formBottomY) {
        console.error("Failed to get all comment form elements to update");
        return;
      }

      // save original values in case update request goes wrong

      formTopX.value = boxLeft;
      formTopY.value = boxTop;
      formBottomX.value = boxLeft + parseFloat(rig.style.width);
      formBottomY.value = boxTop + parseFloat(rig.style.height);

      this.updateAnnotation({annotationType:"comment", annotationId});
    }

    _ensureCommentRig() {
      if (this.commentRig?.isConnected) return this.commentRig;

      const rig = document.createElement("div");
      rig.id = "comment-edit-rig";
      rig.setAttribute("role", "group");
      rig.setAttribute("aria-label", "Comment box");
      rig.title = "Drag to move, handles to resize.";
      rig.hidden = true;
      this._onCommentRigGesture(rig, () => this.buildMoveHandler(rig));

      // The same eight grips the blur rig offers, positioned by data-handle.
      for (const [handleName] of RESIZE_HANDLES) {
        const handle = document.createElement("div");
        handle.classList.add("overlay-resize-handle");
        handle.classList.add("comment-rig-handle");
        handle.dataset["handle"] = handleName;
        this._onCommentRigGesture(handle, () => this.buildResizePointMoveHandler());
        rig.appendChild(handle);
      }

      this.annotationBox.appendChild(rig);
      this.commentRig = rig;
      return rig;
    }

    _onCommentRigGesture(target, buildHandler) {
      target.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        event.stopPropagation();
        target.setPointerCapture(event.pointerId);
        this.commentRigDragging = true;

        const moveHandler = buildHandler();
        const onMove = (moveEvent) => {
          moveHandler(moveEvent);
          this._previewCommentBox();
        };
        const finish = (save) => {
          target.removeEventListener("pointermove", onMove);
          target.removeEventListener("pointerup", onUp);
          target.removeEventListener("pointercancel", onCancel);
          this.commentRigDragging = false;
          if (save) {
            this.updateCommentBoxPositionAndSize();
          } else {
            this.renderCommentRig();
            this._previewCommentBox();
          }
        };
        const onUp = () => finish(true);
        const onCancel = () => finish(false);

        target.addEventListener("pointermove", onMove);
        target.addEventListener("pointerup", onUp);
        target.addEventListener("pointercancel", onCancel);
      });
    }

    _previewCommentBox() {
      const box = document.getElementById("comment-text-box-" + this.commentRigAnnotationId);
      if (!box || !this.commentRig) return;
      box.style.left = this.commentRig.style.left;
      box.style.top = this.commentRig.style.top;
      box.style.width = this.commentRig.style.width;
      box.style.height = this.commentRig.style.height;
    }

    _commentRectFromForm() {
      const updateForm = document.getElementById("annotation-update-form");
      if (!updateForm) return null;
      const read = (selector) => parseFloat(updateForm.querySelector(selector)?.value);
      const x = read("#top-x");
      const y = read("#top-y");
      const right = read("#bottom-x");
      const bottom = read("#bottom-y");
      if (![x, y, right, bottom].every(Number.isFinite)) return null;
      return {x, y, width: right - x, height: bottom - y};
    }

    _readCommentWindow(annotationId) {
      const item = this.timelineWrapper?.querySelector(
        `.track-item[data-annotation-type="comment"][data-annotation-id="${annotationId}"]`,
      );
      const start = parseFloat(item?.dataset["start"]);
      const end = parseFloat(item?.dataset["end"]);
      this.commentRigWindow =
        Number.isFinite(start) && Number.isFinite(end) ? {start, end} : null;
    }

    presentCommentBoxPositionAndSizeControls(annotationId) {
      this.commentRigAnnotationId = String(annotationId);
      this._readCommentWindow(this.commentRigAnnotationId);
      this.renderCommentRig();
    }

    renderCommentRig() {
      if (!this.commentRigAnnotationId || this.commentRigDragging) return;
      const rig = this._ensureCommentRig();
      const rect = this._commentRectFromForm();
      const time = this.video.currentTime;
      const range = this.commentRigWindow;
      const inRange = range !== null && time >= range.start && time < range.end;
      rig.hidden = !inRange || rect === null;
      if (!rig.hidden) {
        applyRect(rig, clampRect(rect, {
          minWidth: COMMENT_MIN_WIDTH,
          minHeight: COMMENT_MIN_HEIGHT,
        }));
      }
    }

    setUpLoadedItemForm(annotationType, annotationId) {
      const detailForm = document.getElementById("detail-form");
      this.setUpItemForm();
      if (annotationType == "comment") {
        this.setUpCommentChangeListeners(detailForm);
        this.presentCommentBoxPositionAndSizeControls(annotationId);
      }
      else if (annotationType == "clip") {
        // set up the next/previous clip buttons each time the form is generated
        function handleClipChange(isAdvancing) {
          // use 1 to advance forward, -1 to advance backward
          const advanceValue = isAdvancing ? 1 : -1;

          // we need to know where we are so we can determine which clip to click on.
          const clipList = document.getElementById("clip-annotation-items-list");
          const clipLen = clipList.children.length;
          let currentIndex;
          for (let index = 0; index < clipLen; index++) {
            const child = clipList.children[index];
            if (child.dataset["annotationId"] == annotationId) {
              currentIndex = index;
              break;
            }
          }

          if (currentIndex === undefined) {
            console.error(`Could not find clip annotation id (${annotationId}) matching the currently active clip.`);
            return;
          }

          const nextIndex = currentIndex + advanceValue;
          if (nextIndex >= clipLen) {
            // roll over to start of list
            clipList.children[0].click();
          }
          else if (nextIndex < 0) {
            clipList.children[clipLen - 1].click();
          }
          else {
            clipList.children[nextIndex].click();
          }
        }
        const nextClipButton = document.getElementById("next-clip-button");
        if (nextClipButton) {
          nextClipButton.addEventListener("click", () => handleClipChange(true));
        }
        const lastClipButton = document.getElementById("last-clip-button");
        if (lastClipButton) {
          lastClipButton.addEventListener("click", () => handleClipChange(false));
        }
      }
    }

    async getItemFormDetails(annotationType, annotationId, contentId) {
      const response = await fetch(`/annotations/${annotationType}/${annotationId}/form/?content_id=${contentId}`, {
        method: "GET"
      });
      if (!response.ok) {
        console.error("Failed to load the annotation detail form");
        return false;
      }
      const detailForm = document.getElementById("detail-form");
      detailForm.innerHTML = await response.text();
      this.setUpLoadedItemForm(annotationType, annotationId);
      return true;
    }

    markItemAsActive(annotationType, annotationId, {navigate = true} = {}) {
      if (!annotationType || !annotationId) {
        console.error("Invalid annotation type of annotation id");
        return;
      }

      // handle panel item style
      const activePanelItemClass = "active-panel-item";
      const itemListExpansionClass = "annotation-type-list-expanded";
      const arrowRotationClass = "annotation-header-arrow-rotated";
      const thisPanelItem = document.getElementById(`${annotationType}-panel-item-${annotationId}`);

      if (!thisPanelItem) {
        console.error("Failed to identify the focused panel item");
        return;
      }

      const activePanelItems = document.getElementsByClassName(activePanelItemClass);
      for (let activePanelItem of activePanelItems) {
        const activePanelItemType = activePanelItem.dataset["annotationType"];
        const panelTypeClassToDisable = `${activePanelItemType}-list-item-wrapper-selected`;
        activePanelItem.classList.remove(panelTypeClassToDisable, activePanelItemClass);
        // we want to collapse the item panel if the new active item is not the same type as the old one.
        if (activePanelItemType != annotationType) {
          const parentItemList = activePanelItem.closest(".annotation-type-list");
          if(parentItemList) {
            parentItemList.classList.remove(itemListExpansionClass);
          }
          const annotationGroupWrapper = activePanelItem.closest(".annotation-type-wrapper");
          const groupWrapperArrow = annotationGroupWrapper.querySelector(".annotation-type-header-arrow");
          groupWrapperArrow.classList.remove(arrowRotationClass);
        }
      }

      // we are ready to add the active item's style
      thisPanelItem.classList.add(`${annotationType}-list-item-wrapper-selected`, activePanelItemClass);
      const thisItemList = thisPanelItem.closest(".annotation-type-list");
      thisItemList.classList.add(itemListExpansionClass);
      const thisGroupWrapper = thisPanelItem.closest(".annotation-type-wrapper");
      const thisGroupArrow = thisGroupWrapper.querySelector(".annotation-type-header-arrow");
      thisGroupArrow.classList.add(arrowRotationClass);
      if (navigate) {
        thisPanelItem.scrollIntoView({behavior: "smooth", block: "nearest"});
      }

      // handle track item style
      const activeTrackItemCSSClass = "active-track-item";
      // first turn off styling of current active element
      const currentActiveTrackItem = this.timelineWrapper.querySelector(`.${activeTrackItemCSSClass}`);
      if (currentActiveTrackItem) {
        currentActiveTrackItem.classList.remove(activeTrackItemCSSClass);
      }

      const trackItem = this.timelineWrapper.querySelector(`.track-item[data-annotation-type="${annotationType}"][data-annotation-id="${annotationId}"]`);
      if (!trackItem) {
        console.error("No track item found");
        return;
      }
      trackItem.classList.add(activeTrackItemCSSClass);

      if (!navigate) {
        return;
      }

      const parentTrackRow = trackItem.closest(".track-row");
      if (parentTrackRow) {
        this.setActiveTrack(parentTrackRow.dataset["trackId"]);
      }

      // skip to start of this annotation in video
      const startTime = Number(trackItem.dataset["start"]);
      if (startTime && !isNaN(startTime)) {
        this.video.currentTime = startTime;
      }
    }

    setUpPanelItemClickListeners() {
      const annotationPanelItems = document.getElementsByClassName("panel-item");
      for (let panelItem of annotationPanelItems) {
        this.setUpItemClickListeners(panelItem);
      }
    }

    setUpAnnotationPanelClickListeners() {
      const annotationPanelGroupHeaders = document.getElementsByClassName("annotation-type-header");
      const panelLists = document.getElementsByClassName("annotation-type-list");
      const panelArrows = document.getElementsByClassName("annotation-type-header-arrow");
      for (let panel of annotationPanelGroupHeaders) {
        const thisPanelList = panel.parentElement.querySelector(".annotation-type-list");
        const listClassName = "annotation-type-list-expanded";
        const thisPanelArrow = panel.querySelector(".annotation-type-header-arrow");
        const arrowClassName = "annotation-header-arrow-rotated";
        panel.addEventListener("click", () => {
          if (thisPanelList.className.includes(listClassName)) {
            thisPanelList.classList.remove(listClassName)
            thisPanelArrow.classList.remove(arrowClassName)
          }
          else {
            for (const arrow of panelArrows) {
              arrow.classList.remove(arrowClassName);
            }
            for (const panelList of panelLists) {
              panelList.classList.remove(listClassName);
            }
            thisPanelArrow.classList.add(arrowClassName);
            thisPanelList.classList.add(listClassName);
          }
        });
      }
    }

    adjustScrubberHeight() {
      const scrubberContainer = document.getElementById("timeline-row-ticks-and-scrubbers");
      const trackRows = document.getElementsByClassName("track-row")
      const scrubberContainerDim = scrubberContainer.getBoundingClientRect();
      const topOfScrubber = scrubberContainerDim.top;
      let bottomOfBottomTrack = scrubberContainerDim.bottom;
      for (let trackRow of trackRows) {
        const trackRowDim = trackRow.getBoundingClientRect();
        if (bottomOfBottomTrack < trackRowDim.bottom) {
          bottomOfBottomTrack = trackRowDim.bottom;
        }
      }
      const scrubbers = document.getElementsByClassName("vertical-scrubber");
      const newScrubberHeight = (bottomOfBottomTrack - topOfScrubber) + "px";
      for (let scrubber of scrubbers) {
        scrubber.style.height = newScrubberHeight;
      }
    }

    /* TRACK EVENT WATCHERS AND HANDLERS */
    setupTracks() {
      const trackRows = document.getElementsByClassName("track-row");
      for (let trackRow of trackRows) {
        this.setupTrackWatchers(trackRow);
      }

      const trackRowAnnotationContainers = document.getElementsByClassName("track-row-annotations-container");
      for (let annotationContainer of trackRowAnnotationContainers) {
        this.setupTrackDragListeners(annotationContainer);
      }
    }

    setupTrackDragListeners(annotationContainer) {
      // set up dragover behavior
      const timelineRow = annotationContainer.closest(".timeline-row");
      const thisContainerTrackId = timelineRow.dataset["trackId"];
      const projectionEl = document.createElement("div");
      projectionEl.classList.add("track-item-projection");
      projectionEl.style.visibility = "hidden";
      annotationContainer.appendChild(projectionEl);
      annotationContainer.addEventListener("dragenter", () => {
        projectionEl.style.visibility = "visible";
      });

      annotationContainer.addEventListener("dragover", (event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        if (!this.itemBeingDragged) {
          console.error("No item is being dragged!");
          return;
        }
        const itemOriginalTrackId = this.itemBeingDragged.dataset["originalTrackId"];
        // Pause items have no `style.width` (they render at a content-driven,
        // shrink-to-fit size - see placeTrackItems), so fall back to their
        // actual rendered width to keep the drag shadow the same size as the
        // item being dragged.
        projectionEl.style.width = this.itemBeingDragged.style.width || `${this.itemBeingDragged.getBoundingClientRect().width}px`;
        if (itemOriginalTrackId == thisContainerTrackId) {
          // show the projection moving in concert with the mouse (with offset)
          const containerDim = annotationContainer.getBoundingClientRect();
          const newLeftRatio = (event.clientX - this.dragGrabOffsetX - containerDim.left) / containerDim.width;
          this.video.currentTime = this.video.duration * newLeftRatio;
          projectionEl.style.left = (newLeftRatio * 100) + '%';
          projectionEl.style.top = this.itemBeingDragged.style.top;
        }
        else {
          // show the projection statically placed with same position as itemBeingDragged.
          this.video.currentTime = this.itemBeingDragged.dataset["start"];
          projectionEl.style.left = this.itemBeingDragged.style.left;
        }
      });

      annotationContainer.addEventListener("dragleave", (event) => {
        // check if executing on self or on this annotationContainer
        const fromItem = event.fromElement?.closest(".track-item");
        if (fromItem?.id == this.itemBeingDragged.id || event?.fromElement == annotationContainer) {
          return;
        }
        projectionEl.style.visibility = "hidden";
      });

      // set up drop behavior, should reject anything that isn't a trackItem
      const itemIdRegEx = new RegExp("[a-z]+-[0-9]+");
      annotationContainer.addEventListener("drop", async (event) => {
        event.preventDefault();
        this.itemBeingDragged = null;
        projectionEl.style.visibility = "hidden";
        const itemId = event.dataTransfer.getData("text");
        if (!itemId || !itemIdRegEx.test(itemId)) {
          return;
        }
        const originalItem = document.getElementById(itemId);

        const trackRowParent = event.target.closest(".track-row");
        const trackId = trackRowParent.dataset["trackId"];
        const originalTrackId = originalItem.dataset["originalTrackId"];
        const annotationType = originalItem.dataset["annotationType"];
        const annotationId = originalItem.dataset["annotationId"];
        const originalStartTime = parseFloat(originalItem.dataset["start"]);
        let originalEndTime;
        if (originalItem.dataset["end"]) {
          originalEndTime = parseFloat(originalItem.dataset["end"]);
        }
        if (trackId != originalTrackId) {
          // transfer item to new track
          await this.updateAnnotation({annotationType, annotationId, "isFromItem": true, "trackId": trackId, "startTime": originalStartTime, "endTime": originalEndTime});
        } else {
          // move item to new position within same track (with offset)
          const containerDim = annotationContainer.getBoundingClientRect();
          const newLeftRatio = (event.clientX - this.dragGrabOffsetX - containerDim.left) / containerDim.width;
          const startTime = this.video.duration * newLeftRatio;
          let endTime;
          if (originalEndTime) {
            endTime = originalEndTime - originalStartTime + startTime;
          }
          // The dropped item is replaced from the response rather than from the drag payload:
          // applyAnnotationVersionResponse renders the new version, moves it to the destination
          // track, and marks it active.
          await this.updateAnnotation({annotationType, annotationId, "isFromItem": true, "startTime": startTime, "endTime": endTime});
        }
      });
    }

    // use this method to apply all relevant watchers (event listeners) to
    // tracks that are new to the DOM.
    setupTrackWatchers(trackRootElement) {
      this.watchForMultiSelectMenuOpen(trackRootElement);
      this.watchForDisplayTrackRename(trackRootElement);
      this.watchForTrackRename(trackRootElement);
      this.watchForTrackMovement(trackRootElement);
      this.watchForTrackDelete(trackRootElement);
      this.watchForTrackActivation(trackRootElement);
    }

    watchForTrackActivation(trackRootElement) {
      const trackId = trackRootElement.dataset["trackId"];
      trackRootElement.addEventListener("click", () => this.setActiveTrack(trackId));
    }

    /* track options menu */
    handleMultiSelectMenuOpen(e) {
      e.stopPropagation();
      // we don't want more than one track menu visible at one time
      const visibleMenuCSSClass = "visible-multi-select-menu";
      const allVisibleTrackMenus = document.getElementsByClassName(visibleMenuCSSClass);
      for (let menu of allVisibleTrackMenus) {
        menu.classList.remove(visibleMenuCSSClass);
      }

      // get track menu and position it properly
      const wrapperDim = this.timelineWrapper.getBoundingClientRect();
      const trackMenuWrapper = e.target.closest(".multi-select-menu-parent");
      const trackOptionsMenu = trackMenuWrapper.querySelector(".multi-select-menu");
      trackOptionsMenu.style.visibility = "hidden";
      trackOptionsMenu.classList.add(visibleMenuCSSClass);
      const trackMenuDim = trackOptionsMenu.getBoundingClientRect();
      if ((trackMenuDim.bottom - wrapperDim.bottom) > -20) {
        trackOptionsMenu.classList.add("multi-select-menu-bumped-up");
      }

      // now we are safe to make the track menu visible
      trackOptionsMenu.style.visibility = "";
    }

    watchForMultiSelectMenuOpen(multiSelectMenuWrapper) {
      const menuButton = multiSelectMenuWrapper.querySelector(".open-multi-select-button");
      if (menuButton) {
        menuButton.addEventListener("click", this.handleMultiSelectMenuOpen.bind(this));
      }
      else {
        console.error("No menu button element found");
      }
    }

    watchForClickOutsideOfTrackMenu() {
      const editorContainer = document.getElementById("editor-container");
      editorContainer.addEventListener("click", (e) => {
        const visibleTrackMenus = document.getElementsByClassName("visible-multi-select-menu");
        for (let menu of visibleTrackMenus) {
          const menuDim = menu.getBoundingClientRect();
          if (e.x < menuDim.left || e.x > menuDim.right || e.y < menuDim.top || e.y > menuDim.bottom) {
            menu.classList.remove("visible-multi-select-menu");
          }
        }
      });
    }

    replaceTracksWithNewHTML(newTracksHTML) {
      // remove old tracks
      const trackRows = document.getElementsByClassName("track-row");

      for (let i = trackRows.length - 1; i >= 0; i--) {
        trackRows[i].remove();
      }

      // place new tracks in appropriate positions
      const timelineZoomRow = document.getElementById("timeline-new-track-and-zoom-row");
      for (let newTrackHTML of newTracksHTML) {
        const trackParentNode = createElementFromHTMLString(newTrackHTML);
        this.setupTrackWatchers(trackParentNode);
        this.timelineWrapper.insertBefore(trackParentNode, timelineZoomRow);
        const annotationContainer = trackParentNode.querySelector(".track-row-annotations-container");
        this.setupTrackDragListeners(annotationContainer);
      }
    }

    /* track name change */
    async handleTrackNameChange(e) {
      e.stopPropagation();
      let parentTrackRow = e.target.closest(".track-row");

      if (!parentTrackRow) {
        console.error("Failed to change track name due to undefined track row element");
        return;
      }

      const trackNameEl = parentTrackRow.querySelector(".track-name");
      const trackId = parentTrackRow.dataset["trackId"];

      function resetTrackName() {
        const renameWrapper = parentTrackRow.querySelector(".track-rename-wrapper");
        renameWrapper.classList.remove("track-rename-wrapper-visible");
        e.target.value = trackNameEl.innerText;
      }

      if (e.key == "Enter") {
        const newTrackName = e.target.value.trim();
        const response = await fetch("/track/update", {
          method: "post",
          headers: {"X-CSRFToken": getCSRFToken()},
          body: JSON.stringify({"new_track_name": newTrackName, "track_id": trackId})
        });
        if (!response.ok) {
          resetTrackName();
          return;
        }

        trackNameEl.innerText = newTrackName;
        e.target.value = trackNameEl.innerText;
      }
      if (e.key == "Escape") {
        resetTrackName();
        return;
      }
    }

    watchForTrackRename(trackRootElement) {
      const trackRenameInput = trackRootElement.querySelector(".track-rename-input");
      if (trackRenameInput) {
        trackRenameInput.addEventListener("keydown", this.handleTrackNameChange.bind(this));
      }
      else {
        console.error("No rename input found for track");
      }
    }

    // Handles renaming of track name, canceling attempted rename,
    // replacement of track with new track, and appending this same
    // event listener to the new track name edit button.
    displayTrackRenameField(e) {
      e.stopPropagation();
      const visibleRenameCSSClass = "track-rename-wrapper-visible";

      // close any other open rename fields
      const visibleRenameWrappers = document.getElementsByClassName(visibleRenameCSSClass);
      for (let wrapper of visibleRenameWrappers) {
        wrapper.classList.remove(visibleRenameCSSClass);
      }

      // open the rename wrapper we are interested in
      const parentRow = e.target.closest(".track-row");
      const renameWrapper = parentRow.querySelector(".track-rename-wrapper");
      const renameInput = renameWrapper.querySelector(".track-rename-input");
      renameWrapper.classList.add(visibleRenameCSSClass);
      renameInput.focus();
      renameInput.setSelectionRange(0, 50);
    }

    watchForDisplayTrackRename(trackRootElement) {
      const renameButton = trackRootElement.querySelector(".track-menu-rename");
      if (renameButton) {
        renameButton.addEventListener("click", this.displayTrackRenameField.bind(this))
      }
      else {
        console.error("No rename button found for track");
      }
    }

    /* track delete */
    async deleteTrack(e) {
      const trackRow = e.target.closest(".track-row");
      const trackId = trackRow.dataset["trackId"];
      const trackDeleteResponse = await fetch(`/track/delete/${trackId}`, {
        method: "delete",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": "application/json"
        }
      });

      if (!trackDeleteResponse.ok) {
        console.error("Failed to delete track");
        return;
      }

      trackRow.remove();
      this.updateTracks();
    }

    watchForTrackDelete(trackRootElement) {
      const deleteButton = trackRootElement.querySelector(".track-menu-delete");
      if (deleteButton) {
        deleteButton.addEventListener("click", this.deleteTrack.bind(this))
      }
    }

    /* Track order reassignment */
    async handleTrackOrderReassignment(trackIdToMove, isMoveUp) {
      if (typeof(isMoveUp) != "boolean") {
        console.error("isMoveUp must be boolean type");
        return;
      }
      const trackRows = document.getElementsByClassName("track-row");
      const trackIdOrder = [];
      for (let trackRow of trackRows) {
        trackIdOrder.push(trackRow.dataset["trackId"]);
      }

      // we don't want to attempt to move the first track up, or the last track down
      if ((isMoveUp && trackIdOrder[0] == trackIdToMove) || (!isMoveUp && trackIdOrder[trackIdOrder.length - 1] == trackIdToMove)) {
        return;
      }

      // use isMoveUp to iterate over only the parts of the array that we care about.
      // Remember - we don't care to move the top row up, or the bottom row down
      for (let i = Number(isMoveUp); i < trackIdOrder.length - Number(!isMoveUp); i++) {
        const currentTrackId = trackIdOrder[i];
        if (currentTrackId == trackIdToMove) {
          if (isMoveUp) {
            const swappingTrackId = trackIdOrder[i - 1];
            trackIdOrder[i - 1] = currentTrackId;
            trackIdOrder[i] = swappingTrackId;
          }
          else {
            const swappingTrackId = trackIdOrder[i + 1];
            trackIdOrder[i + 1] = currentTrackId;
            trackIdOrder[i] = swappingTrackId
          }
          break;
        }
      }

      const orderUpdateResponse = await fetch("/tracks/update_stack_positions",
        {
          method: "post",
          headers: {
            "X-CSRFToken": getCSRFToken(),
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            track_ids: trackIdOrder
          })
        }
      );

      if (!orderUpdateResponse.ok) {
        console.error("Failed to update stack positions across all tracks");
        return;
      }
      const jsonData = await orderUpdateResponse.json();
      const newTracksHTML = jsonData["tracks_html"];

      this.replaceTracksWithNewHTML(newTracksHTML);
      this.updateTracks();
      this.placeTrackItems();
    }

    watchForTrackMovement(trackRootElement) {
      const trackId = trackRootElement.dataset["trackId"];
      const moveUpButton = trackRootElement.querySelector(".track-menu-move-up");

      if (moveUpButton) {
        const handleMoveUp = () => {this.handleTrackOrderReassignment(trackId, true)};
        moveUpButton.addEventListener("click", handleMoveUp.bind(this));
      }

      const moveDownButton = trackRootElement.querySelector(".track-menu-move-down");
      if (moveDownButton) {
        const handleMoveDown = () => {this.handleTrackOrderReassignment(trackId, false)};
        moveDownButton.addEventListener("click", handleMoveDown.bind(this));
      }
    }

    /* track creation */
    watchForTrackCreation() {
      const addNewTrackForm = document.getElementById("new-track-form");
      const addNewTrackInput = document.getElementById("new-track-name");
      const dialog = document.getElementById("new-track-dialog");
      const annotationSetId = this.timelineWrapper.dataset["annotationSetId"];

      async function handleTrackCreation(e) {
        e.preventDefault();
        e.stopPropagation();
        const newTrackName = addNewTrackInput.value;
        if (!newTrackName) {
          return;
        }

        const newTrackResponse = await fetch("/track/create", {
          method: "post",
          headers: {
            "X-CSRFToken": getCSRFToken(),
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            "annotation_set_id": annotationSetId,
            "track_name": newTrackName
          })
        });

        dialog.close();

        if (!newTrackResponse.ok) {
          console.error("Failed to create new track!");
          return;
        }

        addNewTrackInput.value = "";

        const responseData = await newTrackResponse.json();
        const newTracksHTML = responseData["tracks_html"];

        this.replaceTracksWithNewHTML(newTracksHTML);
        this.updateTracks();
        this.placeTrackItems();
        this.adjustScrubberHeight();
      }
      addNewTrackForm.addEventListener("submit", handleTrackCreation.bind(this));
    }

    placeTrackItems() {
        // Process each track container separately
        this.tracks.forEach(track => {
            const trackItems = Array.from(track.children)
                .filter(el => el.classList.contains('track-item'))
                .sort((a, b) => parseFloat(a.dataset.start) - parseFloat(b.dataset.start));
            for (let item of trackItems) {
              const itemStart = parseFloat(item.dataset["start"]);
              const itemEnd = parseFloat(item.dataset["end"]);
              const itemDuration = itemEnd - itemStart;

              if (item.dataset.annotationType != "pause") {
                const itemWidthValue = itemDuration / this.duration * 100;
                item.style.setProperty("width", `${itemWidthValue}%`);
                this.hideOrShowResizeHandles(item);
              }
              else {
                // Pause items have no real duration
                const containerWidth = track.getBoundingClientRect().width;
                const itemWidth = item.getBoundingClientRect().width;
                const virtualDuration = containerWidth > 0 ? (itemWidth / containerWidth) * this.duration : 0;
                item.dataset.virtualEnd = itemStart + virtualDuration;
              }
              const itemLeftValue = parseFloat(item.dataset["start"]) / this.duration * 100
              item.style.setProperty("left", `${itemLeftValue}%`);

              if (item.dataset.annotationType == "blur") {
                placeLocators(item);
              }
            }
            // Assign each item (already sorted by start time above) to the first row
            // - top to bottom - whose last item ends before this one starts. This is
            // recomputed from scratch every call, purely from start/end times (never
            // from a sibling's previously-rendered position), so a start/end change on
            // any single item correctly reflows every item's row in the track.
            const ROW_HEIGHT = 35;
            const rowEndTimes = [];
            for (let item of trackItems) {
                const itemStart = Number(item.dataset.start);
                const itemEnd = item.dataset.annotationType === "pause" ? Number(item.dataset.virtualEnd) : Number(item.dataset.end);
                let rowIndex = rowEndTimes.findIndex(rowEndTime => itemStart > rowEndTime);
                if (rowIndex === -1) {
                    rowIndex = rowEndTimes.length;
                }
                rowEndTimes[rowIndex] = itemEnd;
                item.style.top = `${(rowIndex * ROW_HEIGHT) + 5}px`;
            }

            const maxStack = trackItems.length > 0 ? rowEndTimes.length : 1;

            // Set min-height on the parent track (timeline-row)
            const trackContainer = track.closest('.timeline-row');
            if (trackContainer) {
                // 1 item = 40px; 2 = 75px; 3 = 110px; 4 = 145px; etc.
                const minHeight = (maxStack * ROW_HEIGHT) + 5;
                trackContainer.style.minHeight = `${minHeight}px`;
            }
        });
      this.adjustScrubberHeight();
      this.setupItems();
      this.setUpPanelItemClickListeners();
    }

    handleTrackItemPlacementAfterEvent(e) {
        const classList = e.detail.target.classList;
        const id = e.detail.target.id;
        if (classList.contains('track-items') || classList.contains("track-item") || classList.contains("detail-form") || id.includes("item-")) {
            this.placeTrackItems();
        }
    }

    listenForNewItemCreation() {
      const assignListeners = (elements) => {
        for (let button of elements) {
          const annotationType = button.dataset["annotationType"];
          button.addEventListener("click", async (e) => {
            e.preventDefault();
            // Stop this button's click (which may live inside the collapsible
            // .annotation-type-header) from also toggling the header's expansion.
            e.stopPropagation();
            const trackRow = document.querySelector(`.track-row[data-track-id="${this.activeTrackId}"]`) || document.querySelector(".track-row");
            if (!trackRow) {
              console.error("Unable to assign listeners to new item creation buttons. Invalid track row.");
              return;
            }
            const trackId = trackRow.dataset["trackId"];

            let startTime = 0;
            let endTime = 0;
            if (this.video) {
                startTime = Math.floor(this.video.currentTime * 100) / 100;
                // Make sure new item can fit on the page
                const itemDuration = Math.min(this.duration * 0.2, 10);
                endTime = Math.min(startTime + itemDuration, this.duration);
            }

            const response = await fetch(`/annotations/${annotationType}/create/track/${trackId}`,
              {
                method: "POST",
                headers: {
                  "X-CSRFToken": getCSRFToken(),
                  "Content-Type": "application/json"
                },
                body: JSON.stringify({
                  "start_time": startTime,
                  "end_time": endTime
                })
              });
            if (response.ok) {
              const parsedResponse = await response.json();
              const newPanelItemHtml = parsedResponse["panel_item_html"];
              const panel = document.getElementById(`${annotationType}-annotation-items-list`);
              panel.innerHTML = panel.innerHTML + newPanelItemHtml;

              const newTrackItemHtml = parsedResponse["track_item_html"];
              const trackContainer = document.querySelector(`.track-row[data-track-id="${trackId}"] .track-row-annotations-container`);
              const newNode = createElementFromHTMLString(newTrackItemHtml);
              trackContainer.appendChild(newNode);
              this.placeTrackItems();

              // Immediately select and focus the newly created annotation.
              const newAnnotationId = newNode.dataset["annotationId"];
              this.markItemAsActive(annotationType, newAnnotationId);
              this.getItemFormDetails(annotationType, newAnnotationId, this.contentId);
              newNode.focus();

              window.dispatchEvent(this.annotationUpdatedEvent);
            }
            else {
              console.error(response);
            }
          })
        }
      };
      const createItemButtons = document.getElementsByClassName("add-item-btn");
      const panelCreateItemButtons = document.getElementsByClassName("annotation-type-add-button");
      assignListeners(createItemButtons);
      assignListeners(panelCreateItemButtons);
    }

    /* TIMELINE FUNCTIONS */
    // This is the tick line on the timeline
    createTickMark(time, isMajor) {
        const tick = document.createElement('div');
        tick.className = `tick-mark ${isMajor ? 'major' : 'minor'}`;

        const percent = (time / this.duration) * 100;
        tick.style.left = `${percent}%`;

        return tick;
    }

    formatTime(seconds) {
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);

        if (hours > 0) {
            return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
        } else if (minutes > 0) {
            return `${minutes}:${String(secs).padStart(2, '0')}`;
        } else {
            return `0:${String(secs).padStart(2, '0')}`;
        }
    }

    // this is for the time stamp above each labeled tick line on the timeline
    createTickLabel(time) {
        const label = document.createElement('div');
        label.className = 'tick-label';
        label.textContent = this.formatTime(time);

        const percent = (time / this.duration) * 100;
        if (percent == 0) {
          label.className += " first-tick";
        }
        else {
          label.style.left = `${percent}%`;
        }

        return label;
    }

    calculateTickInterval() {
        // Calculate how many seconds fit in viewport at current zoom
        const viewportSeconds = this.duration / this.zoomLevel;

        // Choose interval to show 4-6 labels
        const targetLabels = 5;
        const rawInterval = viewportSeconds / targetLabels;

        // Ticks are drawn across the whole duration (not just the visible
        // window), so deep zoom on a long video can demand an enormous
        // number of minor ticks. Cap the total so it stays cheap to render.
        const MAX_MINOR_TICKS = 4000;
        const fitsTickBudget = (interval) => (this.duration / (interval / 5)) <= MAX_MINOR_TICKS;

        // Snap to nice intervals: 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, etc.
        const niceIntervals = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600];

        for (const interval of niceIntervals) {
            if (interval >= rawInterval && fitsTickBudget(interval)) {
                return interval;
            }
        }

        // For very long videos, use multiples of an hour, still respecting the tick budget
        let interval = Math.max(3600, Math.ceil(rawInterval / 3600) * 3600);
        while (!fitsTickBudget(interval)) {
            interval += 3600;
        }
        return interval;
    }

    renderTickMarksAndLabels() {
        if (!this.tickMarksContainer) return;

        // Clear existing tick marks
        this.tickMarksContainer.innerHTML = '';

        // Calculate appropriate interval based on zoom and duration
        const interval = this.calculateTickInterval();
        const minorInterval = interval / 5;

        // Generate tick marks
        for (let time = 0; time <= this.duration; time += minorInterval) {
            const compTime = time.toFixed(2);
            const isMajor = Math.abs(compTime % interval) < 0.01;
            const tick = this.createTickMark(compTime, isMajor);
            this.tickMarksContainer.appendChild(tick);

            // Add label for major ticks
            if (isMajor) {
                const label = this.createTickLabel(compTime);
                this.tickMarksContainer.appendChild(label);
            }
        }
    }

    // Recompute cached timeline geometry, then position the scrubber. This reads
    // layout (getBoundingClientRect/scrollLeft), so it is only called on the
    // events that can change the geometry (zoom, scroll, seek, resize, and the
    // periodic timeupdate) — never per animation frame.
    adjustScrubberPosition() {
      this.refreshScrubberBounds();
      this.paintScrubber();
    }

    /* The scrubber is bound between the left and right edges of the visible
    window; it never goes off the page. Its position depends on the current time
    and how far the timeline is zoomed/scrolled. We can think of the currently
    displayed time as a window on the full timeline. These bounds only change on
    zoom/scroll/resize, so we cache them and let paintScrubber() interpolate. */
    refreshScrubberBounds() {
      const totalTimeline = this.tickMarksContainer;
      const visibleTimeline = this.timelineTicksContent;
      if (!totalTimeline || !visibleTimeline) return;

      const totalScrollableWidth = totalTimeline.getBoundingClientRect().width;
      const windowWidth = visibleTimeline.getBoundingClientRect().width;
      const scrollLeft = visibleTimeline.scrollLeft;

      this.scrubberBounds = {
        windowWidth,
        leftTime: this.video.duration * (scrollLeft / totalScrollableWidth),
        rightTime: this.video.duration * ((scrollLeft + windowWidth) / totalScrollableWidth),
      };
    }

    // Position the scrubber from the cached bounds and the current time. Uses a
    // composited transform and performs no layout reads, so it is cheap enough
    // to run every animation frame during playback.
    paintScrubber() {
      if (!this.scrubberBounds || !this.editorScrubber) return;

      const { windowWidth, leftTime, rightTime } = this.scrubberBounds;
      const span = rightTime - leftTime;
      const currentTime = this.video.currentTime;

      let ratio;
      if (span <= 0 || currentTime <= leftTime) {
        ratio = 0;
      } else if (currentTime >= rightTime) {
        ratio = 1;
      } else {
        ratio = (currentTime - leftTime) / span;
      }

      this.editorScrubber.style.transform = `translateX(${ratio * windowWidth}px)`;
    }

    handleZoom() {
      const annotationContainers = this.timelineWrapper.querySelectorAll(".track-row-annotations-container");
      if (annotationContainers.length == 0) {
        return;
      }

      const newWidth = `${100 * this.zoomLevel}%`;
      const zoomSliderWidth = `${100 / this.zoomLevel}%`;
      document.documentElement.style.setProperty("--zoom-thumb-width", zoomSliderWidth);
      const tickMarksContainer = document.getElementById("tick-marks-container");
      if (tickMarksContainer) {
        tickMarksContainer.style.width = newWidth;
      }
      for (let annotationContainer of annotationContainers) {
        annotationContainer.style.width = newWidth;
      }

      const locationRatio = this.video.currentTime / this.video.duration;
      const trackWidth = tickMarksContainer.getBoundingClientRect().width;
      const tickMarkContainerParent = tickMarksContainer.closest("#timeline-ticks-content");
      const parentWidth = tickMarkContainerParent.getBoundingClientRect().width;
      this.scrollTracksToPoint((trackWidth * locationRatio) - parentWidth / 2);
      this.renderTickMarksAndLabels();
      // Zoom changes the visible time window, so recompute the scrubber bounds.
      this.adjustScrubberPosition();

      this.placeTrackItems();
    }

    attachZoomListener() {
      const MIN_ZOOM_LEVEL = 1;
      const MIN_VIEWPORT_SECONDS = 5;
      const ZOOM_STEP_FACTOR = 1.5;

      // Cap zoom so the visible window can shrink to a few seconds no matter
      // how long the video is, instead of a fixed zoom level that leaves long
      // videos unable to zoom in past several minutes of visible width.
      const getMaxZoomLevel = () => Math.max(10, this.duration / MIN_VIEWPORT_SECONDS);

      const zoomInButton = document.getElementById("zoom-in-button");
      if (zoomInButton) {
        zoomInButton.addEventListener("click", () => {
          this.zoomLevel = Math.min(getMaxZoomLevel(), this.zoomLevel * ZOOM_STEP_FACTOR);
          this.handleZoom();
        });
      }

      const zoomOutButton = document.getElementById("zoom-out-button");
      if (zoomOutButton) {
        zoomOutButton.addEventListener("click", () => {
          this.zoomLevel = Math.max(MIN_ZOOM_LEVEL, this.zoomLevel / ZOOM_STEP_FACTOR);
          this.handleZoom();
        });
      }
    }

    scrollTracksToPoint(scrollValue) {
      const tracksToAdjust = document.getElementsByClassName("timeline-track-row-right");
      const tickMarksWrapper = document.getElementById("timeline-ticks-content");
      for (let track of tracksToAdjust) {
        track.scrollLeft = scrollValue;
      }
      tickMarksWrapper.scrollLeft = scrollValue;

      const contentWidth = this.tickMarksContainer?.getBoundingClientRect().width;
      if (contentWidth > 0) {
        this.timelineScrollRatio = tickMarksWrapper.scrollLeft / contentWidth;
        if (this.zoomSliderInput) {
          this.zoomSliderInput.value = this.timelineScrollRatio * 100;
        }
      }
    }

    restoreTimelineScroll() {
      const contentWidth = this.tickMarksContainer?.getBoundingClientRect().width;
      if (!contentWidth || !this.timelineScrollRatio) return;
      this.scrollTracksToPoint(contentWidth * this.timelineScrollRatio);
    }

    watchForTimelineScrollChangeAndHandleIt() {
      this.zoomSliderInput.addEventListener("input", (e) => {
        const newValue = e.target.value;
        const tickMarksContainer = document.getElementById("tick-marks-container");
        const widthInPixels = tickMarksContainer.getBoundingClientRect().width;
        const newScrollLeft = widthInPixels * (newValue / 100);
        this.scrollTracksToPoint(newScrollLeft);
        this.adjustScrubberPosition();
      });
    }

    createtimelineScrubber() {
        this.timelineScrubber = document.querySelector('#timeline-hover-scrubber');
        if (!this.timelineScrubber) {
            this.timelineScrubber = document.createElement('div');
            this.timelineScrubber.id = 'timeline-hover-scrubber';
            if (this.timelineTicksContent) {
                this.timelineTicksContent.appendChild(this.timelineScrubber);
            }
        }
    }

    // Position the hover scrubber (the preview line) under the cursor. `left` is
    // a percentage of the ticks row, which spans the same width as the viewport.
    updatetimelineScrubber(e) {
        if (!this.timelineScrubber) return;

        const rect = this.timelineTicksContent.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const percent = Math.max(0, Math.min(100, (x / rect.width) * 100));

        this.timelineScrubber.style.left = `${percent}%`;
    }

    // Convert a viewport x-coordinate into a video time, accounting for the
    // current zoom (the scaled full-timeline width) and horizontal scroll.
    timeFromClientX(clientX) {
        const totalWidth = this.tickMarksContainer.getBoundingClientRect().width;
        if (!totalWidth) return 0;
        const viewport = this.timelineTicksContent.getBoundingClientRect();
        const xInContent = (clientX - viewport.left) + this.timelineTicksContent.scrollLeft;
        const ratio = Math.max(0, Math.min(1, xInContent / totalWidth));
        return ratio * this.video.duration;
    }

    seekVideoTo(time) {
        this.video.currentTime = time;
        // Also update via the player API if available
        if (window.videoPlayer && window.videoPlayer.skipTo) {
            window.videoPlayer.skipTo(time);
        }
    }

    // Whether an event target is somewhere a click should seek: inside a seek
    // region but not on an annotation's content (which selects the annotation).
    isSeekTarget(target) {
        if (!target.closest(SEEK_REGION_SELECTOR)) return false;
        if (target.closest('.track-item')) return false;
        return true;
    }

    startTimelineDrag(e) {
        this.isDragging = true;

        // Pause during the scrub, remembering whether to resume afterwards.
        this.wasPlayingBeforeDrag = !this.video.paused;
        if (this.wasPlayingBeforeDrag) {
            this.video.pause();
        }

        // Hide hover scrubber during drag
        if (this.timelineScrubber) {
            this.timelineScrubber.style.opacity = '0';
        }

        // Seek to initial position
        this.seekVideoTo(this.timeFromClientX(e.clientX));

        e.preventDefault();
    }

    endTimelineDrag() {
        this.isDragging = false;

        // Resume playback if it was playing before
        if (this.wasPlayingBeforeDrag) {
            this.video.play();
        }

        this.wasPlayingBeforeDrag = false;
    }

    attachTimelineListeners() {
        if (!this.timelineWrapper) return;

        // Track the cursor and show the hover scrubber across the seek regions.
        this.timelineWrapper.addEventListener('mousemove', (e) => {
            if (this.isDragging || this.dragState) return;
            if (!this.isSeekTarget(e.target)) {
                if (this.timelineScrubber) this.timelineScrubber.style.opacity = '0';
                return;
            }
            this.updatetimelineScrubber(e);
            if (this.timelineScrubber) this.timelineScrubber.style.opacity = '1';
        });

        this.timelineWrapper.addEventListener('mouseleave', () => {
            if (this.timelineScrubber && !this.isDragging) {
                this.timelineScrubber.style.opacity = '0';
            }
        });

        // Begin scrubbing on a left mousedown inside a seek region. Resize handles
        // stop propagation before this fires, so they still resize rather than seek.
        this.timelineWrapper.addEventListener('mousedown', (e) => {
            if (e.button !== 0) return;
            if (!this.isSeekTarget(e.target)) return;
            this.startTimelineDrag(e);
        });

        document.addEventListener('mousemove', (e) => {
            if (this.isDragging) {
                this.seekVideoTo(this.timeFromClientX(e.clientX));
                e.preventDefault();
            }
        });

        document.addEventListener('mouseup', () => {
            if (this.isDragging) {
                this.endTimelineDrag();
            }
        });
    }

    attachVideoListeners() {
        // timeupdate fires only a few times a second, so it drives the periodic
        // geometry refresh and keeps the scrubber correct while paused/seeking.
        this.video.addEventListener('timeupdate', () => this.adjustScrubberPosition());
        this.video.addEventListener('seeked', () => this.adjustScrubberPosition());
        window.addEventListener('resize', () => {
            this.restoreTimelineScroll();
            this.adjustScrubberPosition();
        });
        this.adjustScrubberPosition();
        // During playback, interpolate every animation frame for smooth motion
        // (shared mechanism with AnnotationPlayer's progress scrubber).
        animateDuringPlayback(this.video, () => this.paintScrubber());
    }

    watchAndHandleAnnotationSetMenuOpen() {
      const annotationSetMenuWrapper = document.getElementById("annotation-panel-header");
      this.watchForMultiSelectMenuOpen(annotationSetMenuWrapper);
    }

    async handleAnnotationSetCreation(setName, annotationSetId = undefined, annotationSetJson = undefined) {
      const createResponse = await fetch("/annotation-set/create", {
        method: "POST",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          content_id: this.contentId,
          name: setName,
          annotation_set_id_to_copy: annotationSetId,
          annotation_set_json: annotationSetJson
        })
      });

      if (!createResponse.ok) {
        console.error("Failed to create new annotation set");
        return;
      }
      window.location.reload();
    }

    toggleAnnotationSetOptionSelectorAndContent() {
      // hide the options selector and show the selected option content
      // or do the opposite if the options selector should be shown
      const selectOptionContent = document.getElementById("annotation-set-modal-base-content");
      const optionContentContainer = document.getElementById("annotation-set-modal-option-display");
      if (selectOptionContent.className.includes("hidden")) {
        optionContentContainer.classList.add("hidden");
        selectOptionContent.classList.remove("hidden");
      } else {
        selectOptionContent.classList.add("hidden");
        optionContentContainer.classList.remove("hidden");
      }
    }

    async setupAndDisplayAnnotationSetOption(url) {
      const annotationSetOptionsModalContent = document.getElementById("annotation-set-modal-option-display");
      const contentResponse = await fetch(url);
      if (!contentResponse.ok) {
        console.error("Failed to get new content for annotation set options modal");
        return false;
      }
      const newHTML = await contentResponse.text();
      annotationSetOptionsModalContent.innerHTML = newHTML;

      // set up back button
      const backButton = document.getElementById("annotation-set-modal-back");
      backButton.addEventListener("click", () => {
        this.toggleAnnotationSetOptionSelectorAndContent();
      });

      this.toggleAnnotationSetOptionSelectorAndContent();
      return true;
    }

    async setupAnnotationSetUseExistingModal() {
      const result = await this.setupAndDisplayAnnotationSetOption(`/annotation-options-modal/use-existing/${this.contentId}`);
      if (!result) {
        return;
      }
      const confirmButton = document.getElementById("annotation-set-use-existing-button");
      confirmButton.addEventListener("click", async (event) => {
        const parent = event.target.closest("#annotation-set-use-existing-option");
        const setSelector = parent.querySelector(".annotation-set-selector");
        if (setSelector.value == undefined || setSelector.value == "") {
          setSelector.classList.add("invalid-input");
          return;
        } else {
          setSelector.classList.remove("invalid-input");
        }
        const contentSetAssignmentResponse = await fetch("/select-annotation-set", {
          method: "POST",
          headers: {
            "X-CSRFToken": getCSRFToken(),
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            "content_id": this.contentId,
            "annotation_set_id": setSelector.value
          })
        });
        if (!contentSetAssignmentResponse.ok) {
          console.error("Failed to set annotation set for this content");
          return;
        }
        window.location.reload();
      });
    }

    async setupAnnotationSetCopyModal() {
      const result = await this.setupAndDisplayAnnotationSetOption(`/annotation-options-modal/copy-from-set/${this.contentId}`);
      if (!result) {
        return;
      }
      const createButton = document.getElementById("annotation-set-copy-from-button");
      createButton.addEventListener("click", (event) => {
        const parent = event.target.closest("#annotation-set-modal-copy-from-set");
        const nameInput = parent.querySelector("#copy-from-annotation-set-name");
        let isInvalid = false;
        if (!nameInput.value) {
          isInvalid = true;
          nameInput.classList.add("invalid-input");
        } else {
          nameInput.classList.remove("invalid-input");
        }
        const setSelection = parent.querySelector(".annotation-set-selector");
        if (setSelection.value == undefined || setSelection.value == '') {
          isInvalid = true;
          setSelection.classList.add("invalid-input");
        } else {
          setSelection.classList.remove("invalid-input");
        }
        if (isInvalid) return;
        this.handleAnnotationSetCreation(nameInput.value, setSelection.value);
      });
    }

    async setupAnnotationSetImportModal() {
      const result = await this.setupAndDisplayAnnotationSetOption("/annotation-options-modal/import");
      if (!result) {
        return;
      }
      const createButton = document.getElementById("create-annotation-set-from-import-button");
      createButton.addEventListener("click", async (event) => {
        const parent = event.target.closest("#import-create-annotation-set");
        let isInvalid = false;
        const nameInput = parent.querySelector("#new-annotation-set-name");
        if (!nameInput.value) {
          isInvalid = true;
          nameInput.classList.add("invalid-input");
        } else {
          nameInput.classList.remove("invalid-input");
        }
        const fileInput = parent.querySelector("#annotation-set-import-file-input");
        if (fileInput.files.length <= 0) {
          isInvalid = true;
          fileInput.classList.add("invalid-input");
        } else {
          fileInput.classList.remove("invalid-input");
        }
        if (isInvalid) return;
        const jsonFile = await fileInput.files[0].text();
        try {
          JSON.parse(jsonFile);
        } catch {
          console.error("Invalid JSON provided");
          return;
        }

        this.handleAnnotationSetCreation(nameInput.value, undefined, jsonFile);
      });
    }

    async setupAnnotationSetCreationModal() {
      const result = await this.setupAndDisplayAnnotationSetOption("/annotation-options-modal/create");
      if (!result) {
        return;
      }
      const createAnnotationSetButton = document.getElementById("annotation-set-create-button");
      createAnnotationSetButton.addEventListener("click", (event) => {
        const parent = event.target.closest("#annotation-set-create-new-content");
        const setName = parent.querySelector("#create-annotation-set-name");
        if (!setName.value) {
          setName.classList.add("invalid-input");
          return;
        } else {
          setName.classList.remove("invalid-input");
        }
        this.handleAnnotationSetCreation(setName.value);
      });
    }

    setupAnnotationSetOptionsModal() {
      const viewExistingButton = document.getElementById("annotation-set-view-existing");
      if (viewExistingButton) {
        viewExistingButton.addEventListener("click", this.setupAnnotationSetUseExistingModal.bind(this));
      }

      const viewCopyButton = document.getElementById("annotation-set-view-copy");
      if (viewCopyButton) {
        viewCopyButton.addEventListener("click", this.setupAnnotationSetCopyModal.bind(this));
      }

      const viewImportButton = document.getElementById("annotation-set-view-import");
      viewImportButton.addEventListener("click", this.setupAnnotationSetImportModal.bind(this));

      const viewCreateButton = document.getElementById("annotation-set-view-create");
      viewCreateButton.addEventListener("click", this.setupAnnotationSetCreationModal.bind(this));
    }



    watchAndHandleAnnotationSetDelete() {
      const deleteAnnotationSetButton = document.getElementById("annotation-set-delete");
      if (!deleteAnnotationSetButton) {
        console.error("Failed to get annotation set delete button");
        return;
      }

      deleteAnnotationSetButton.addEventListener("click", async () => {
        const annotationSetId = this.timelineWrapper.dataset["annotationSetId"];
        if (isNaN(annotationSetId) || annotationSetId === undefined || annotationSetId == "") {
          return;
        }
        const deleteResponse = await fetch(`/annotation-set/delete/${annotationSetId}`, {
          method: "DELETE",
          headers: {
            "X-CSRFToken": getCSRFToken()
          }
        });
        if (!deleteResponse.ok) {
          console.error("Failed to delete annotation set");
          return;
        }

        window.location.reload();
      });
    }

    watchAndHandleAnnotationSetExport() {
      const exportAnnotationSetModal = document.getElementById("export-annotation-set");
      const exportButton = document.getElementById("annotation-set-export-button");
      exportButton.addEventListener("click", async () => {
        const exportLink = document.getElementById("export-annotation-set-link");
        const setSelector = exportAnnotationSetModal.querySelector(".annotation-set-selector");
        const annotationSetId = setSelector.value;
        if (isNaN(Number(annotationSetId)) || annotationSetId == '' || annotationSetId == undefined) {
          // prevent any non-number value from being injected into link
          return;
        }
        exportLink.href = `/annotation-set/export/${Number(annotationSetId)}`;
        exportLink.click();
      });
    }

    async handleAnnotationSetChange(event) {
        event.stopPropagation();
        let annotationSetId = event.target.value;

        if (isNaN(annotationSetId) || annotationSetId === undefined) {
            console.error("Selected value was not defined!");
            return;
        }

        if (!this.contentId) {
            console.error("could not retrieve content id while switching annotation sets!");
            return;
        }
        const htmlContentResponse = await fetch("/select-annotation-set", {
            method: "POST",
            body: JSON.stringify({"annotation_set_id": annotationSetId, "content_id": this.contentId}),
            headers: {
              "X-CSRFToken": getCSRFToken(),
              "Content-Type": "application/json"
            },
            mode: "same-origin"
        });
        if (!htmlContentResponse.ok) {
          console.error("Failed to update annotation_set!");
          return;
        }
        // rebuild the page with the new annotation set
        window.location.reload();
    }

    watchForAnnotationSetNameChangeAndHandleIt() {
      const annotationSetSettingsEl = document.getElementById("annotation-set-settings-compact");
      const annotationSetId = annotationSetSettingsEl.dataset["annotationSetId"];
      const annotationNameInput = document.getElementById("annotation-set-name");
      const annotationNameSubmitButton = document.getElementById("annotation-name-submit-button");

      const handleNameChange = async () => {
        const currentAnnotationSetName = annotationSetSettingsEl.dataset["annotationSetName"];
        const newName = annotationNameInput.value.trim();
        const nameChangeResponse = await fetch("/annotation-set/update-name/", {
            method: "POST",
            headers: {
              "X-CSRFToken": getCSRFToken(),
              "Content-Type": "application/json"
            },
            body: JSON.stringify({
              annotation_set_id: annotationSetId,
              name: newName
            })
        });
        if (!nameChangeResponse.ok) {
          console.error("Failed to update annotation set name");
          annotationNameInput.value = currentAnnotationSetName;
          return;
        }
        annotationNameInput.value = newName;
        annotationSetSettingsEl.dataset["annotationSetName"] = newName;
        const annotationSetOptionName = annotationSetSettingsEl.querySelector(`.annotation-set-option[value="${annotationSetId}"] .set-option-name`);
        annotationSetOptionName.innerText = newName;
      }

      annotationNameInput.addEventListener("keydown", (e) => {
        if (e.key == "Enter") {
          handleNameChange();
        }
      })
      annotationNameSubmitButton.addEventListener("click", handleNameChange);
    }

    handleNoAnnotationSet() {
      // This is designed to run only when the editor loads to encourage a user to select,
      // create, or import an annotation set
      const annotationSetId = this.timelineWrapper.dataset["annotationSetId"];
      if (annotationSetId === '' || annotationSetId === undefined) {
        // prevent user from leaving modal if they need to pick an annotation set
        const optionsModal = document.getElementById("annotation-set-options-modal");
        const exitButton = optionsModal.querySelector(".close-dialog-button");
        exitButton.remove();

        // open the annotation set options modal
        const annotationSetOptionsButton = document.getElementById("open-annotation-set-options-modal");
        annotationSetOptionsButton.click();
      }
    }

    async handleRemoveEditor(e) {
      e.stopPropagation();
      const annotationSetSettingsEl = document.getElementById("annotation-set-settings-compact");
      const annotationSetId = annotationSetSettingsEl.dataset["annotationSetId"];
      // because the remove button has an element inside it, e.target could refer to the image element,
      // or the button element which is the img's parent. The editorId is only on the button element.
      // To get around this, the closest .remove-editor-button will find the correct in either case.
      const buttonEl = e.target.closest(".remove-editor-button");
      const editorId = buttonEl.dataset["editorId"];
      const removalResponse = await fetch(`/annotation-set/${annotationSetId}/remove-editor/${editorId}/`, {
        method: "DELETE",
        headers: {
          "X-CSRFToken": getCSRFToken()
        }
      });
      if (!removalResponse.ok) {
        console.error("Failed to remove editor");
        return;
      }
      e.target.closest(".annotation-set-editor-details").remove();
    }

    attachRemoveEditorListener(editorDetailEl) {
      const removeEditorButton = editorDetailEl.querySelector(".remove-editor-button");
      if (removeEditorButton) {
        removeEditorButton.addEventListener("click", this.handleRemoveEditor.bind(this));
      }
    }

    attachRemoveEditorListeners() {
      const editorDetailEls = document.getElementsByClassName("annotation-set-editor-details");
      for (let editorDetailEl of editorDetailEls) {
        this.attachRemoveEditorListener(editorDetailEl);
      }
    }

    async handleAddEditor(e) {
      const annotationSetSettingsEl = document.getElementById("annotation-set-settings-compact");
      const annotationSetId = annotationSetSettingsEl.dataset["annotationSetId"];
      const editorId = e.target.dataset["editorId"];
      const selectedEditorsResponse = await fetch("/annotation-set/add-editor", {
        method: "POST",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          annotation_set_id: annotationSetId,
          editor_id: editorId
        })
      });
      if (!selectedEditorsResponse.ok) {
        console.error("Failed to add new editor");
        return;
      }
      const newWrapper = await selectedEditorsResponse.text();
      const selectedEditorsWrapper = document.getElementById("selected-editors");
      selectedEditorsWrapper.outerHTML = newWrapper;
      this.attachRemoveEditorListeners();
    }

    attachAddEditorListeners() {
      const editorSearchResultsWrapper = document.getElementById("editor-search-results");
      const resultEntries = editorSearchResultsWrapper.querySelectorAll(".editor-search-result");
      if (resultEntries.length == 0) {
        editorSearchResultsWrapper.innerText = "No search results";
        return;
      }
      for (let resultEl of resultEntries) {
        resultEl.addEventListener("click", this.handleAddEditor.bind(this));
      }
    }

    watchForEditorSearchInputAndHandleIt() {
      /* Watch for keydown on input field, if its been less than timer amount since
      the last keydown, clear the last request and start the timer again. If
      the timer ends, execute the search function. */
      const editorSearchInput = document.getElementById("editor-search-input");
      let keydownTimerId;
      const handleSearchInput = () => {
        clearTimeout(keydownTimerId);
        keydownTimerId = setTimeout(async () => {
          const searchResultsWrapper = document.getElementById("editor-search-results");
          const searchString = editorSearchInput.value.trim();
          if (searchString == "") {
            searchResultsWrapper.innerHTML = "";
          }
          if (!searchString) {
            return;
          }

          // remove old search results
          const oldSearchResultEls = document.getElementsByClassName("editor-option");
          for (let i = oldSearchResultEls.length - 1; i >= 0; i--) {
            const resultEl = oldSearchResultEls[i];
            resultEl.remove();
          }

          // execute request
          const searchResponse = await fetch("/annotation-set/search-for-editor", {
            method: "POST",
            headers: {
              "X-CSRFToken": getCSRFToken(),
              "Content-Type": "application/json"
            },
            body: JSON.stringify({search_string: searchString})
          });

          if (!searchResponse.ok) {
            console.error("Failed to execute editor search");
            searchResultsWrapper.innerText = "An error has occurred, please try searching again.";
            return;
          }
          const results = await searchResponse.text();
          searchResultsWrapper.outerHTML = results;
          this.attachAddEditorListeners();
        }, 300);
      }
      editorSearchInput.addEventListener("keydown", handleSearchInput.bind(this));
    }

    watchAndHandleEditorPanelSwitch() {
      const annotationPanel = document.getElementById("editor-annotation-panel");
      const subtitlesPanel = document.getElementById("subtitle-editor-panel");
      const annotationPanelSwitchButton = document.getElementById("annotation-panel-switch");
      const subtitlePanelSwitchButton = document.getElementById("subtitle-panel-switch");
      // All four are absent for YouTube-backed content, which has no subtitle editor to switch
      // to - see annotation_panel.html. Returning rather than throwing matters: this runs during
      // editor init, so a missing button here would take the whole editor down with it.
      if (!annotationPanel || !subtitlesPanel || !annotationPanelSwitchButton || !subtitlePanelSwitchButton) {
        return;
      }
      const togglePanelVisiblity = () => {
        annotationPanel.classList.toggle("editor-annotation-panel-hidden");
        annotationPanel.classList.toggle("editor-annotation-panel-visible");
        subtitlesPanel.classList.toggle("subtitle-editor-panel-visible");
        subtitlesPanel.classList.toggle("subtitle-editor-panel-hidden");
      }
      annotationPanelSwitchButton.addEventListener("click", togglePanelVisiblity);
      subtitlePanelSwitchButton.addEventListener("click", togglePanelVisiblity);
    }

    watchAndHandleSubtitleTrackChange() {
      const subtitleSelectInput = document.getElementById("subtitles-track-selector");
      if (!subtitleSelectInput) return;
      subtitleSelectInput.addEventListener("change", async () => {
        const newSubtitleTrackId = subtitleSelectInput.value;
        if (subtitleSelectInput == undefined) {
          console.error("Invalid subtitle track id");
          return;
        }
        this.selectedSubtitleTrackId = newSubtitleTrackId;
        const subtitlesResponse = await fetch(`/subtitles/get-editable-subtitles/${this.selectedSubtitleTrackId}`);
        if (!subtitlesResponse.ok) {
          console.error("Failed to get subtitle cues");
          return;
        }
        const subtitlesPanelHTML = await subtitlesResponse.text();
        const currentSubtitlesPanel = document.getElementById("subtitle-panel-content-wrapper");
        currentSubtitlesPanel.outerHTML = subtitlesPanelHTML;
        const subtitlesSettingsModal = document.getElementById("subtitles-settings");
        this.buildWatchersForSubtitlePanelContent();
        this.buildWatchersForSubtitleEditorCues();
        subtitlesSettingsModal.close();
      })
    }

    collectCues() {
      const rawCues = document.getElementsByClassName("editor-subtitle-cue");
      const cues = [];
      for (let cue of rawCues) {
        // gather elements to extract data from
        const typeEl = cue.querySelector(".editor-subtitle-cue-type");
        const payloadEl = cue.querySelector(".editor-subtitle-cue-content");
        const identifierEl = cue.querySelector(".editor-subtitle-cue-identifier");
        const startTimeEl = cue.querySelector(".editor-subtitle-cue-start");
        const endTimeEl = cue.querySelector(".editor-subtitle-cue-end");
        const settingsEl = cue.querySelector(".editor-subtitle-cue-settings");

        // extract the data and package into array to send to backend
        cues.push({
          type: typeEl.value,
          payload: payloadEl.value,
          identifier: identifierEl.value,
          start_time: startTimeEl.value,
          end_time: endTimeEl.value,
          cue_settings: settingsEl.value,
        });
      }
      return cues;
    }

    async saveCues(cues, isAutosave=true) {
      if (typeof isAutosave !== "boolean") {
        console.error("isAutosave must be a boolean");
        return;
      }

      const updateResponse = await fetch("/subtitles/update-subtitle-cues", {
        method: "POST",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          subtitle_id: this.selectedSubtitleTrackId,
          cues: cues,
          seconds_nudge: 0,
          nudge_excluded_cues: [],
          is_autosave: isAutosave
        })
      });

      if (!updateResponse.ok) {
        console.error("Failed to save cues");
        return;
      }

      const subtitleCueListWrapper = document.getElementById("subtitle-panel-list");
      subtitleCueListWrapper.innerHTML = await updateResponse.text();
      this.buildWatchersForSubtitleEditorCues();
    }

    buildWatchersForSubtitlePanelContent() {
      const subtitlesPanel = document.getElementById("subtitle-panel-content-wrapper");
      const addNewCueButton = subtitlesPanel.querySelector("#add-new-subtitle-button");
      addNewCueButton.addEventListener("click", () => {
        const cues = this.collectCues();
        // build new cue and append it to cues array
        const time = this.video.currentTime;
        // the back end sorts cues, so this will go in its correct place.
        cues.push(
          {
            start_time: formatSecondsToString(time, true),
            end_time: formatSecondsToString(time + 2, true),
            type: "CUE",
            payload: "",
            identifier: "",
            cue_settings: "",
          }
        )
        this.saveCues(cues, false)
      });
    }

    buildWatchersForSubtitleEditorCues() {
      const subtitlesPanel = document.getElementById("subtitle-panel-content-wrapper");
      const deleteCue = (e) => {
        const editorSubtitleCueEl = e.target.closest('.editor-subtitle-cue');
        editorSubtitleCueEl.remove();
        this.saveCues(this.collectCues(), false);
      }
      const editorSubtitleCueDeleteButtons = subtitlesPanel.querySelectorAll(".editor-subtitle-cue-delete");
      for (let cueDelButton of editorSubtitleCueDeleteButtons) {
        cueDelButton.addEventListener("click", deleteCue.bind(this));
      }

      const saveUpdatedInformation = () => {
        this.saveCues(this.collectCues(), false);
      }
      const cueInputs = subtitlesPanel.querySelectorAll(".editor-subtitle-cue-start, .editor-subtitle-cue-end, .editor-subtitle-cue-content");
      for (let cueInput of cueInputs) {
        cueInput.addEventListener("keydown", (e) => {
          if (e.key != "Enter") {
            return;
          }
          saveUpdatedInformation();
        });
      }
    }
}

function editorInit() {
  // Initialize when DOM is ready
  let editor;
  if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => {
          editor = new Editor();
      });
  } else {
      editor = new Editor();
  }
  editor.setUpAnnotationPanelClickListeners();
}

function showUnplayableVideoNotice(video) {
    if (document.getElementById('editor-video-error-banner')) return;
    const banner = document.createElement('div');
    banner.id = 'editor-video-error-banner';
    banner.className = 'editor-video-error-banner';
    banner.setAttribute('role', 'alert');
    const label = document.createElement('strong');
    label.textContent = 'This video cannot be played, so the editor could not start.';
    const youtubeDetail = video?.tagName === 'YOUTUBE-VIDEO'
        ? ' A YouTube video can be removed, made private, or have embedding turned off by its ' +
          'owner at any time.'
        : '';
    banner.append(
        label,
        document.createTextNode(
            `${youtubeDetail} Annotations already saved for it are unaffected.`
        )
    );
    document.body.prepend(banner);
}

const checkVideo = setInterval(() => {
    const video = document.querySelector('#video-player');
    if (video?.error) {
      clearInterval(checkVideo);
      showUnplayableVideoNotice(video);
      return;
    }
    if (video && !isNaN(video.duration) && window?.videoPlayer) {
      clearInterval(checkVideo);
      editorInit();
    }
}, 100);

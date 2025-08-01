import React, { useState, useEffect } from 'react'
import { connect } from 'react-redux'
import FileUploadContainer from 'components/modals/containers/FileUploadContainer'
import DeleteConfirmContainer from '../../components/modals/containers/DeleteConfirmContainer'
import ManageFilesContainer from 'components/modals/containers/ManageFilesContainer'
import ManageInstructorsContainer from 'components/modals/containers/ManageInstructorsContainer'

import {
	resourceService,
	interfaceService,
	languageService,
} from 'services'

import {
	ResourceOverview,
} from 'components'

import { objectIsEmpty } from 'lib/util'

const ResourceOverviewContainer = props => {

	const {
		resource,
		editResource,
		resourceCache,
		getResourceFiles,
		updateAllFileVersions,
		fileId,
		getLangs,
		user,
		readAccess,
		resourceAccess,
	} = props

	const [editing, setEditing] = useState(false)
	const [showing, setShowing] = useState(false)
	const [resourceState, setResourceState] = useState(resource)
	const [files, setFiles] = useState([])
	const [accessCount, setAccessCount] = useState(0)
	const [numFileVersions, setNumFileVersions] = useState(0)
	const [blockLeave, setBlock] = useState(false)

	useEffect(() => {

		if(editing && resourceCache[resource.id].files !== undefined)
			setFiles(resourceCache[resource.id].files)

		if(resourceAccess[resource.id] && resourceAccess[resource.id].length !== accessCount)
			setAccessCount(resourceAccess[resource.id].length)

		if(files.length !== numFileVersions){
			setNumFileVersions(files.length)

			let langs = ``
			files.forEach(file => {
				langs = langs.concat(`${file[`file-version`]};`)
			})

			if(resource.allFileVersions !== langs){
				setResourceState({
					...resourceState,
					allFileVersions: langs,
				})
				updateAllFileVersions(resourceState, files)
				editResource(resourceState, resourceState.id)
			}
		}

		if(blockLeave)
			window.onbeforeunload = () => true
		else
			window.onbeforeunload = undefined

		return () => {
			window.onbeforeunload = undefined
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [editing, files, numFileVersions, resource.allFileVersions, resource.id, resourceCache, resourceState, updateAllFileVersions, resourceAccess, blockLeave])

	if (objectIsEmpty(resource)) return null

	const handleFileUploadToResource = async() => {
		props.toggleModal({
			component: FileUploadContainer,
			props: {
				resourceId: resource.id,
			},
		})
		setBlock(true)
	}

	const handleToggleEdit = async () => {

		if (editing) {
			await editResource(resourceState, resourceState.id)
			setShowing(false)
			setEditing(false)
			setBlock(false)

		} else{
			// set up languages on store before editing
			await getLangs()
			await getResourceFiles(resource.id)

			// read access
			await readAccess(resource.id)
			setEditing(true)
		}

	}

	// this should delete related contents as well
	const handleRemoveResource = e => {
		props.toggleModal({
			component: DeleteConfirmContainer,
			props: {
				type: `resource`,
				id: resource.id,
			},
		})
	}

	const handleResourceName = e => {
		setResourceState({
			...resourceState,
			resourceName: e.target.value,
		})
		setBlock(true)
	}

	const handleResourceEmail = e => {
		setResourceState({
			...resourceState,
			requesterEmail: e.target.value,
		})
	}

	const handleResourceMetadata = e => {
		setResourceState({
			...resourceState,
			metadata: e.target.value,
		})
	}

	const handleTypeChange = e => {
		setResourceState({
			...resourceState,
			resourceType: e.target.dataset.type,
		})
		setBlock(true)
	}

	const handleTogglePublish = e => {
		setResourceState({
			...resourceState,
			published: !resourceState.published,
		})
		setBlock(true)
	}

	const handleToggleCopyRighted = e => {
		setResourceState({
			...resourceState,
			copyrighted: !resourceState.copyrighted,
		})
		setBlock(true)
	}

	const handleTogglePhysicalCopyExists = e => {
		setResourceState({
			...resourceState,
			physicalCopyExists: !resourceState.physicalCopyExists,
		})
	}

	const handleToggleFullVideo = e => {
		setResourceState({
			...resourceState,
			fullVideo: !resourceState.fullVideo,
		})
		setBlock(true)
	}

	const handleMetadata = e => {
		setResourceState({
			...resourceState,
			metadata: e.target.value,
		})
	}

	const handleFiles = () => {
		props.toggleModal({
			component: ManageFilesContainer,
			props: {
				files,
			},
		})
	}

	const handleInstructors = () => {
		props.toggleModal({
			component: ManageInstructorsContainer,
			props: {
				resource,
			},
		})
	}

	const viewstate = {
		resourceCache,
		resource: resourceState,
		files,
		accessCount,
		user,
		fileId,
		showing,
		editing,
		blockLeave,
	}

	const handlers = {
		handleFileUploadToResource,
		handleFiles,
		handleInstructors,
		handleResourceName,
		handleResourceMetadata,
		handleRemoveResource,
		handleToggleEdit,
		handleTogglePublish,
		handleTogglePhysicalCopyExists,
		handleToggleCopyRighted,
		handleToggleFullVideo,
		setResourceState,
		setShowing,
		handleTypeChange,
		handleResourceEmail,
		handleMetadata,
	}

	/*
			account-type
			0 = admin
			1 = lab assistant
			2 = faculty / instructor
			3 = student
		*/

	return <ResourceOverview viewstate={viewstate} handlers={handlers} />
}

const mapStateToProps = store => ({
	thisfiles: store.fileStore.cache,
	fileId: store.fileStore.cache,
	resourceCache: store.resourceStore.cache,
	resourceAccess: store.resourceStore.access,
	filesCache: store.fileStore.cache,
	user: store.authStore.user,
})

const mapDispatchToProps = {
	removeResource: resourceService.removeResource,
	editResource: resourceService.editResource,
	updateAllFileVersions: resourceService.updateFileVersion,
	toggleModal: interfaceService.toggleModal,
	getResourceFiles: resourceService.getFiles,
	readAccess: resourceService.readAccess,
	getLangs: languageService.get,
	getFiles: resourceService.getFiles,
}

export default connect(mapStateToProps, mapDispatchToProps)(ResourceOverviewContainer)

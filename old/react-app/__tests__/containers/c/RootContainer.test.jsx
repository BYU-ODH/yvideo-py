import React from 'react'
import { shallow } from 'enzyme'
import Container from '../../../containers/c/RootContainer'
import * as testutil from '../../testutil/testutil'

const props = {
	checkAuth: jest.fn(),
	loading: false,
	modal: {
		active: false,
		collectionId: -1,
		componenet: null,
		isLabAssistantRoute: false,
		props: {},
	},
	tried: true,
	user: {
		email: `email@testemail.com`,
		id: 22,
		lastLogin: `2020-05-14T19:53:02.807Z`,
		name: `testname`,
		roles: 0,
		username: `testusername`,
	},
}

describe(`root container test`, () => {
	it(`test viewstate should be true`, () => {
		const wrapper = shallow(
			<Container store={testutil.store} {...props}/>,
		).childAt(0).dive()

		const viewstate = wrapper.props().viewstate
		expect(viewstate.user.email).toBe(`email@testemail.com`)
		expect(viewstate.user.id).toBe(22)
		expect(viewstate.user.lastLogin).toBe(`2020-05-14T19:53:02.807Z`)
		expect(viewstate.user.name).toBe(`testname`)
		expect(viewstate.user.roles).toBe(0)
		expect(viewstate.user.username).toBe(`testusername`)
		expect(viewstate.loading).toBe(false)
		expect(viewstate.modal.active).toBe(false)
		expect(viewstate.modal.collectionId).toBe(-1)
		expect(viewstate.modal.isLabAssistantRoute).toBe(false)
	})
})
